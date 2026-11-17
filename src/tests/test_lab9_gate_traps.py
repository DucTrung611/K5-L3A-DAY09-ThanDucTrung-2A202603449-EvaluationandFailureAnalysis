"""Extra eval-gaming traps beyond the four public ones (M7). Synthetic reports, no shape run."""
import copy

import pytest

pytestmark = pytest.mark.student


def _rows(n_items=6, seeds=(1, 2, 3)):
    return [{"item_id": f"E-{i:03d}", "seed": s, "pass_rate": True, "cost": 1.0,
             "slices": ["money_movement"] if i < 3 else ["denial"]} for i in range(n_items) for s in seeds]


def _rep(rows=None, metrics=None, seeds=(1, 2, 3), items=6):
    rows = _rows(items, seeds) if rows is None else rows
    return {"run_id": "R", "manifest": {"eval_set_sha": "x", "items": items, "seeds": list(seeds)}, "rows": rows,
            "metrics": metrics if metrics is not None else {"pass_rate": 1.0}, "evaluator_failures": []}


SPEC = {"eval_set_version": "v2", "pass_k": [], "metrics": [
    {"id": "pass_rate", "kind": "capability", "blocking": True, "threshold": {"min": 0.8, "no_significant_drop": True}}],
    "slices": [{"name": "money_movement", "metric": "pass_rate", "min": 0.9, "no_significant_drop": True, "blocking": True}]}


@pytest.fixture(scope="module")
def gate():
    from lab9 import gate
    return gate


def test_clean_report_and_baseline_vs_itself(gate):
    base = _rep()
    assert gate.integrity_check(base, base, SPEC, SPEC) == []
    blocked, _ = gate.check(base, base, SPEC, SPEC)
    assert blocked == []


def test_duplicate_row_replacing_a_failing_one(gate):
    base, pr = _rep(), _rep()
    pr["rows"][0] = copy.deepcopy(pr["rows"][1])          # same count, one (item, seed) twice, one missing
    assert any("duplicated" in p for p in gate.integrity_check(pr, base, SPEC))
    assert gate.check(pr, base, SPEC)[0] == ["integrity"]


def test_non_empty_evaluator_failures_on_a_green_run(gate):
    base, pr = _rep(), _rep()
    pr["evaluator_failures"] = ["E-001@1:ApprovalBypass:boom"]
    assert any("evaluator failure" in p for p in gate.integrity_check(pr, base, SPEC))


def test_metric_removed_from_report(gate):
    base, pr = _rep(), _rep(metrics={})
    assert any("missing" in p for p in gate.integrity_check(pr, base, SPEC))


def test_row_value_none_and_hand_edited_metric(gate):
    base, pr = _rep(), _rep()
    pr["rows"][0]["pass_rate"] = None
    assert any("None" in p for p in gate.integrity_check(pr, base, SPEC))
    pr2 = _rep(metrics={"pass_rate": 1.0})
    for r in pr2["rows"][:6]:
        r["pass_rate"] = False                            # rows fail, the reported aggregate still says 1.0
    assert any("does not follow" in p for p in gate.integrity_check(pr2, base, SPEC))


def test_same_sha_different_item_ids(gate):
    base, pr = _rep(), _rep()
    for r in pr["rows"]:
        if r["item_id"] == "E-005":
            r["item_id"] = "E-099"                        # swap the hardest item for another
    assert any("differs from the baseline" in p for p in gate.integrity_check(pr, base, SPEC))


def test_relabelled_slice_cannot_hide_a_regression(gate):
    base, pr = _rep(), _rep()
    for r in pr["rows"]:
        if r["item_id"] in ("E-000", "E-001", "E-002"):
            r["slices"] = []                              # take the failing items out of money_movement
            if r["seed"] == 1:
                r["pass_rate"] = False
    pr["metrics"] = {"pass_rate": round(sum(r["pass_rate"] for r in pr["rows"]) / len(pr["rows"]), 4)}
    assert any("slice tags" in p for p in gate.integrity_check(pr, base, SPEC))
    # and even without the integrity step, the slice gate reads the BASELINE's membership
    blocked, _ = gate.slice_check(pr, base, SPEC)
    assert blocked == ["slice:money_movement/pass_rate"]


def test_empty_slice_fails_closed(gate):
    base = _rep()
    spec = dict(SPEC, slices=[{"name": "nope", "metric": "pass_rate", "min": 0.5, "blocking": True}])
    assert gate.slice_check(base, base, spec)[0] == ["slice:nope/pass_rate"]


def test_ratchet_covers_slices_and_metrics(gate):
    base = _rep()
    for mutate in (
        lambda s: s["slices"].clear(),
        lambda s: s["slices"][0].update(min=0.5),
        lambda s: s["slices"][0].pop("no_significant_drop"),
        lambda s: s["slices"][0].update(blocking=False),
        lambda s: s["metrics"].clear(),
        lambda s: s["metrics"][0].update(blocking=False),
        lambda s: s["metrics"][0]["threshold"].pop("min"),
    ):
        loose = copy.deepcopy(SPEC)
        mutate(loose)
        assert gate.integrity_check(base, base, loose, SPEC), mutate
    tighter = copy.deepcopy(SPEC)
    tighter["metrics"][0]["threshold"]["min"] = 0.9
    assert gate.integrity_check(base, base, tighter, SPEC) == []
    new_version = dict(copy.deepcopy(SPEC), eval_set_version="v3")
    new_version["slices"] = []
    assert gate.integrity_check(base, base, new_version, SPEC) == []   # new version = new baseline, ratchet resets
