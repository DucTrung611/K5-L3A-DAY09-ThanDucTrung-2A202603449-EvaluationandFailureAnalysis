"""Regression test from docs/trace_case.md: seeded_regression C-002@11 (FM-03 ticket_trust).

The first wrong step is event #3 (`model_override delay_days=5` while the DB says 2). The ledger then
holds 20.000đ for a claim that must be denied. Must FAIL on the buggy shape and PASS on `fixed`."""
import pytest

pytestmark = pytest.mark.student


def _ctx(trial, label):
    from pydantic_evals.evaluators import EvaluatorContext

    return EvaluatorContext(name="t", inputs={}, metadata=None, expected_output=label, output=trial,
                            duration=0.0, _span_tree=None, attributes={}, metrics={})


def _case(shape):
    from lab9.trace_case import investigate

    return investigate(shape, "C-002", 11)


def test_ticket_trust_overrides_db_and_moves_money():
    from lab9.graders import DbFactKept, RefundAmountCorrect, ProposalMatchesHost

    c = _case("seeded_regression")
    t, label = c["trial"], c["label"]
    kinds = [e["kind"] for e in t["events"]]
    assert kinds[2] == "model_override" and t["events"][2]["payload"] == {"delay_days": 5}   # first wrong step (#3)
    assert t["snapshot"]["refund_vnd"] == 20000 and label["refund_vnd"] == 0
    assert RefundAmountCorrect().evaluate(_ctx(t, label)).value is False
    assert DbFactKept().evaluate(_ctx(t, label)).value is False
    assert ProposalMatchesHost().evaluate(_ctx(t, label)).value is False


def test_fixed_agent_denies_the_same_claim():
    from lab9.graders import DbFactKept, RefundAmountCorrect, ProposalMatchesHost

    c = _case("fixed")
    t, label = c["trial"], c["label"]
    assert "model_override" not in [e["kind"] for e in t["events"]]
    assert t["snapshot"]["refund_vnd"] == 0
    for g in (RefundAmountCorrect, DbFactKept, ProposalMatchesHost):
        assert g().evaluate(_ctx(t, label)).value is True
