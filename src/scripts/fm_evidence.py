"""Print the trace evidence behind docs/failure_taxonomy.md: one exemplar per failure mode.

    cd src && PYTHONPATH=. python scripts/fm_evidence.py            # all
    cd src && PYTHONPATH=. python scripts/fm_evidence.py FM-03      # one

Each bug switch of lab9.variant_agent is run alone on top of `no_override` (the fixed agent), so the
only difference from the control is that one switch. Read-only: temp store, nothing is written to reports/.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from lab8.db import load_json, load_jsonl
from lab8.shapes import SHAPES
from lab9 import variants
from lab9.eval import WORLD, make_task
from lab9.variant_agent import make_shape

# FM -> (bug switch, claim, seed)
EXEMPLARS = {
    "FM-01": ("leaky_trace", "C-001", 11),
    "FM-02": ("ignore_denied", "C-007", 11),
    "FM-03": ("ticket_trust", "C-002", 11),
    "FM-04": ("sum_parts", "C-014", 11),
    "FM-05": ("pre_deducted", "C-013", 11),
    "FM-06": ("stale_clause", "C-006", 11),
    "FM-07": ("sibling_refund", "C-001", 11),
    "FM-08": ("misreport", "C-008", 11),
    # No bug switch exists for FM-09: this is the *defence* working. A shape that reached the ledger for
    # C-005 (P-200 asking about P-100's order O-110) would trip approval_bypass / refund_amount_correct.
    "FM-09": ("fixed", "C-005", 11),
}


def short(p, n=150):
    s = json.dumps(p, ensure_ascii=False)
    return s if len(s) <= n else s[:n] + "…"


def show(fm: str, shape: str, claim: str, seed: int, tmp: Path, claims, labels) -> None:
    run = make_task(shape, load_json(WORLD), claims, labels, tmp)
    t = run({"claim_id": claim, "seed": seed})
    lab = labels[claim]
    print(f"\n=== {fm}  {shape} {claim}@{seed}  lab8 grader: {'PASS' if t['lab8_pass'] else 'FAIL'} ({t['lab8_reason']})")
    print(f"    ledger refund_vnd={t['snapshot']['refund_vnd']} (label {lab['refund_vnd']}), unexpected_rows={t['unexpected_rows']},"
          f" proposal={t['result'].get('proposal_amount')} (label {lab['proposal_amount']}), status={t['result'].get('status')} (label {lab['status']})")
    for i, e in enumerate(t["events"], 1):
        print(f"    #{i} {e['kind']:<16} {short(e['payload'])}")


def main(argv: list[str]) -> int:
    variants.register()
    claims = load_jsonl(Path("data/public_claims.jsonl"))
    labels = {c["claim_id"]: c["expected"] for c in claims}
    for _, (bug, _, _) in EXEMPLARS.items():
        if bug != "fixed":
            SHAPES.setdefault(f"fm_{bug}", make_shape(f"fm_{bug}", {"no_override", bug}))
    want = set(argv) or set(EXEMPLARS)
    with tempfile.TemporaryDirectory(prefix="fm-evidence-", ignore_cleanup_errors=True) as tmp:
        for fm, (bug, claim, seed) in EXEMPLARS.items():
            if fm in want:
                show(fm, "fixed" if bug == "fixed" else f"fm_{bug}", claim, seed, Path(tmp), claims, labels)
        if not argv:
            show("control", "fixed", "C-002", 11, Path(tmp), claims, labels)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
