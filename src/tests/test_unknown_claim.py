"""A claim id that does not exist: status failed, proposal 0, no ledger, valid trace, no exception.

The contract (docs/contracts.md) says run_claim must never raise; the grader records
an exception as a failed trial. Every return path must also write a contract-v1 trace.
Owner: Thân Đức Trung (2A202603449).
"""

from __future__ import annotations

import time

import pytest

from lab8.db import connect, init_store
from lab8.metrics import check_trace
from lab8.shapes import SHAPES
from tests.conftest import CLAIMS, WORLD

GROUP_SHAPES = ["workflow", "single", "orchestrator"]
MISSING = "C-DOES-NOT-EXIST"


@pytest.mark.parametrize("shape", GROUP_SHAPES)
def test_unknown_claim_fails_cleanly(tmp_path, shape):
    store = tmp_path / f"{shape}-unknown.sqlite"
    init_store(store, WORLD, CLAIMS).close()
    started = time.time()
    result = SHAPES[shape](MISSING, seed=11, store_path=str(store))
    if result.error_reason == "not_implemented":
        pytest.skip(f"{shape} not implemented yet")

    assert result.claim_id == MISSING
    assert result.status == "failed"
    assert result.proposal_amount == 0
    assert result.refund_effects == []
    assert result.committed_audit_count == 0
    assert check_trace(result.as_dict(), started_at=started) == (True, "ok")

    conn = connect(store)
    try:
        rows = conn.execute("SELECT COUNT(*) AS n FROM refund_ledger WHERE claim_id = ?", (MISSING,)).fetchone()["n"]
    finally:
        conn.close()
    assert rows == 0


@pytest.mark.parametrize("shape", GROUP_SHAPES)
def test_missing_order_id_needs_info_and_traces(tmp_path, shape):
    """T04 (C-004): no order id -> needs_info on an early return, trace still valid."""
    store = tmp_path / f"{shape}-c004.sqlite"
    init_store(store, WORLD, CLAIMS).close()
    started = time.time()
    result = SHAPES[shape]("C-004", seed=11, store_path=str(store))
    if result.error_reason == "not_implemented":
        pytest.skip(f"{shape} not implemented yet")
    assert result.status == "needs_info"
    assert result.proposal_amount == 0
    assert result.refund_effects == []
    assert check_trace(result.as_dict(), started_at=started) == (True, "ok")
