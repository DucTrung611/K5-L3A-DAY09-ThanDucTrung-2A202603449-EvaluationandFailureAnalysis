"""Per-order cap and full quote preservation tests (T08, T13, T14).

Shapes must pass the FULL quote to durability.issue_refund.
The host gatekeeper subtracts earlier refunds on the same order.
If a shape subtracts prior refunds itself, the host subtracts twice (causing T14 failure).
Owner: Trần Mạnh Hùng (2A202603496).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lab8.db import connect, init_store
from lab8.durability import get_refund
from lab8.shapes import SHAPES
from tests.conftest import CLAIMS, WORLD

GROUP_SHAPES = ["workflow", "single", "orchestrator"]


def _run_shape(tmp_path: Path, shape: str, claim_id: str, seed: int = 11):
    store = tmp_path / f"{shape}-{claim_id}-{seed}.sqlite"
    init_store(store, WORLD, CLAIMS).close()
    result = SHAPES[shape](claim_id, seed=seed, store_path=str(store))
    if result.error_reason == "not_implemented":
        pytest.skip(f"{shape} not implemented")
    conn = connect(store)
    try:
        refund = get_refund(conn, claim_id)
        denied_audits = conn.execute(
            "SELECT COUNT(*) AS n FROM audit_events WHERE claim_id = ? AND event_type = 'policy_denied'",
            (claim_id,),
        ).fetchone()["n"]
    finally:
        conn.close()
    return result, refund, denied_audits


@pytest.mark.parametrize("shape", GROUP_SHAPES)
def test_t14_full_quote_and_order_cap_difference(tmp_path: Path, shape: str):
    """T14 (C-014): Order O-515 was previously refunded 30k for shipping.
    Now damage is verified (entitlement 150k).
    Shape must propose full 150k quote; host ledger commits difference 120k.
    """
    result, refund, _ = _run_shape(tmp_path, shape, "C-014")
    assert result.status == "resolved"
    assert result.proposal_amount == 150000, "proposal_amount must stay full quote (150000)"
    assert refund is not None
    assert refund["amount_vnd"] == 120000, "host ledger must commit only the remaining 120000"


@pytest.mark.parametrize("shape", GROUP_SHAPES)
def test_t13_already_fully_refunded_denied_with_policy_denied(tmp_path: Path, shape: str):
    """T13 (C-013): Order O-505 was already refunded 35k in full.
    Ticket says 'chưa nhận được tiền'.
    Shape must quote full 35k; host denies with 'order_already_refunded' audit.
    """
    result, refund, denied_audits = _run_shape(tmp_path, shape, "C-013")
    assert result.status == "denied"
    assert result.proposal_amount == 35000, "proposal_amount must remain full quote (35000)"
    assert refund is None, "ledger must have no rows"
    assert denied_audits >= 1, "host must record policy_denied audit event"


@pytest.mark.parametrize("shape", GROUP_SHAPES)
def test_t08_approval_cap_below_quote(tmp_path: Path, shape: str):
    """T08 (C-008): Order O-808 shipping fee 35k, but approval A-008 is capped at 20k.
    Shape proposes 35k; host caps refund at 20k.
    """
    result, refund, _ = _run_shape(tmp_path, shape, "C-008")
    assert result.status == "resolved"
    assert result.proposal_amount == 35000, "proposal_amount stays full quote (35000)"
    assert refund is not None
    assert refund["amount_vnd"] == 20000, "ledger must be capped by approval to 20000"
