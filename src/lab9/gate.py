"""CI gate: compare a PR eval report with the main-branch baseline using the metric spec.

    python -m lab9.gate --pr reports/eval_workflow.json --baseline reports/baseline_v1.json

exit 0 = merge, exit 1 = blocked. Safety metrics: max 0, no averaging away.
Capability metrics: meet the floor AND the paired CI of (PR - main) is not entirely below 0.
Thresholds may only get stricter on the same eval-set version (ratchet).
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from lab8.console import utf8_stdio
from lab9 import stats
from lab9.eval import SPEC, load_spec, per_item


def _rows_key(rep: dict) -> list[tuple]:
    return [(r.get("item_id"), r.get("seed")) for r in rep.get("rows") or []]


def _ratchet_threshold(what: str, old: dict, new: dict) -> list[str]:
    out = []
    for key in ("max", "max_increase"):
        if key in old and (key not in new or new[key] > old[key]):
            out.append(f"ratchet: {what} {key} {old[key]} -> {new.get(key, 'removed')} (looser)")
    if "min" in old and ("min" not in new or new["min"] < old["min"]):
        out.append(f"ratchet: {what} min {old['min']} -> {new.get('min', 'removed')} (looser)")
    if old.get("no_significant_drop") and not new.get("no_significant_drop"):
        out.append(f"ratchet: {what} dropped no_significant_drop")
    return out


def _ratchet(spec: dict, base_spec: dict) -> list[str]:
    """Same eval-set version: a threshold may get stricter, never looser (and nothing may vanish)."""
    out: list[str] = []
    new = {m["id"]: m for m in spec.get("metrics") or []}
    for bm in base_spec.get("metrics") or []:
        nm = new.get(bm["id"])
        if nm is None:
            out.append(f"ratchet: metric {bm['id']} was removed from the spec")
            continue
        if bm.get("blocking") and not nm.get("blocking"):
            out.append(f"ratchet: metric {bm['id']} is no longer blocking")
        out += _ratchet_threshold(f"metric {bm['id']}", bm.get("threshold") or {}, nm.get("threshold") or {})
    new_sl = {s["name"]: s for s in spec.get("slices") or []}
    for bs in base_spec.get("slices") or []:
        ns = new_sl.get(bs["name"])
        if ns is None:
            out.append(f"ratchet: slice {bs['name']} was removed from the spec")
            continue
        if ns.get("metric") != bs.get("metric"):
            out.append(f"ratchet: slice {bs['name']} changed metric {bs.get('metric')} -> {ns.get('metric')}")
        if bs.get("blocking") and not ns.get("blocking"):
            out.append(f"ratchet: slice {bs['name']} is no longer blocking")
        out += _ratchet_threshold(f"slice {bs['name']}", bs, ns)
    return out


def _bad_value(v) -> bool:
    return v is None or (isinstance(v, float) and not math.isfinite(v))


def integrity_check(pr: dict, base: dict, spec: dict, base_spec: dict | None = None) -> list[str]:
    """M7 (Expert): refuse to compare reports that cannot be trusted. Return a list of problems
    (empty = trustworthy). A gate that passes a broken report is worse than no gate.
      - NaN / inf / missing metric values (a missing metric must not read as "no failure")
      - row count != items x seeds, duplicated (item_id, seed) rows
      - (item_id, seed) set or manifest seeds/items differ from the baseline (cherry-picked / dropped)
      - evaluator_failures, or a row whose value for a graded spec metric is None / NaN / absent
      - reported metrics that do not follow from the rows (hand-edited aggregates)
      - base_spec given, same eval-set version, and a threshold / slice got looser (ratchet)
      - an item's slice tags differ from the baseline's (relabelling an item out of a slice)"""
    problems: list[str] = []
    mids = [m["id"] for m in spec.get("metrics") or []]
    rows = pr.get("rows") or []
    man, bman = pr.get("manifest") or {}, base.get("manifest") or {}
    pm, bm = pr.get("metrics") or {}, base.get("metrics") or {}

    for k, v in pm.items():
        if isinstance(v, float) and not math.isfinite(v):
            problems.append(f"metric {k} is {v} (NaN/inf)")
    for mid in mids:
        if pm.get(mid) is None and bm.get(mid) is not None:
            problems.append(f"metric {mid} is missing or None in the PR report but present in the baseline")

    keys = _rows_key(pr)
    n_seeds = len(man.get("seeds") or [])
    expected = int(man.get("items", 0)) * n_seeds
    if len(rows) != expected:
        problems.append(f"{len(rows)} row(s) but the manifest says {man.get('items')} items x {n_seeds} seeds = {expected}")
    dup = sorted({k for k in keys if keys.count(k) > 1}, key=str)
    if dup:
        problems.append(f"duplicated (item_id, seed) rows: {dup[:3]}")
    base_keys = set(_rows_key(base))
    if set(keys) != base_keys:
        problems.append(f"(item_id, seed) set differs from the baseline: {len(base_keys - set(keys))} missing, "
                        f"{len(set(keys) - base_keys)} extra")
    if man.get("seeds") != bman.get("seeds") or man.get("items") != bman.get("items"):
        problems.append(f"manifest seeds/items {man.get('seeds')}/{man.get('items')} differ from the baseline's "
                        f"{bman.get('seeds')}/{bman.get('items')}")

    if pr.get("evaluator_failures"):
        problems.append(f"{len(pr['evaluator_failures'])} evaluator failure(s), first: {pr['evaluator_failures'][0]}")
    first_rows = [(base.get("rows") or [{}])[0], (rows or [{}])[0]]
    graded = [m for m in mids if any(m in r for r in first_rows)]
    for m in graded:
        bad = [r for r in rows if _bad_value(r.get(m))]
        if bad:
            problems.append(f"{len(bad)} row(s) with None/NaN/absent {m} (grader crashed or value removed)")

    # Aggregates must follow from the rows: a hand-edited metric would otherwise sail through.
    if rows and not problems:
        try:
            from lab9.eval import aggregate
            has_cost = all("cost" in r for r in rows)
            again = aggregate([{**r, "cost": r.get("cost", 0.0)} for r in rows], graded, k_values=spec.get("pass_k", []))
            for k, v in again.items():
                got = pm.get(k)
                if k == "cost_per_success" and not has_cost:
                    continue
                if isinstance(got, (int, float)) and isinstance(v, (int, float)) and abs(got - v) > 1e-3:
                    problems.append(f"reported metric {k}={got} does not follow from the rows (recomputed {v})")
        except Exception:  # noqa: BLE001  (a malformed row is reported above)
            pass

    if base_spec is not None and spec.get("eval_set_version") == base_spec.get("eval_set_version"):
        problems += _ratchet(spec, base_spec)

    tags = lambda rep: {r.get("item_id"): sorted(r.get("slices") or []) for r in rep.get("rows") or []}
    bt, pt = tags(base), tags(pr)
    moved = sorted((i for i in bt.keys() & pt.keys() if bt[i] != pt[i]), key=str)
    if moved:
        problems.append(f"slice tags differ from the baseline for {len(moved)} item(s): {moved[:3]} (relabelled?)")
    return problems


def _slice_members(pr: dict, base: dict, name: str) -> set[str]:
    """Items in a slice, taken from the BASELINE's tags: the PR's own tags are not trusted."""
    tagged = lambda rep: {r["item_id"] for r in rep.get("rows") or [] if name in (r.get("slices") or [])}
    return tagged(base) or tagged(pr)


def slice_check(pr: dict, base: dict, spec: dict) -> tuple[list[str], list[str]]:
    """M5 (Advanced): an average can hide a regression on a small, important slice.
    For each entry of spec["slices"] ({"name", "metric", "min"?, "no_significant_drop"?, "blocking"}):
      - value = mean of the metric over the slice's items
      - fail if below "min", or if no_significant_drop and the paired CI over the slice's items
        lies entirely below 0; an empty slice fails closed (nothing to look at is not a pass)
    Return (blocked ids like "slice:money_movement/pass_rate", report lines)."""
    blocked: list[str] = []
    lines: list[str] = []
    bs = spec.get("bootstrap", {})
    for s in spec.get("slices") or []:
        name, metric = s["name"], s["metric"]
        sid = f"slice:{name}/{metric}"
        members = _slice_members(pr, base, name)
        a = {k: v for k, v in per_item(pr, metric).items() if k in members}
        b = {k: v for k, v in per_item(base, metric).items() if k in members}
        verdict, note, value = "ok", "", None
        if not a:
            verdict, note = "fail", "empty slice (fail closed)"
        else:
            value = sum(a.values()) / len(a)
            if "min" in s and value < s["min"]:
                verdict = "fail"
            if s.get("no_significant_drop"):
                keys = sorted(a.keys() & b.keys())
                if keys:
                    d, lo, hi = stats.paired_bootstrap([a[k] for k in keys], [b[k] for k in keys],
                                                       n_resamples=bs.get("n_resamples", 1000), rng=bs.get("rng", 2027))
                    note = f"n={len(keys)} Δ {d:+.3f} CI [{lo:+.3f}, {hi:+.3f}]"
                    if hi < 0:
                        verdict = "fail"
                else:
                    verdict, note = "fail", "no item in common with the baseline (fail closed)"
        if verdict != "ok" and s.get("blocking"):
            blocked.append(sid)
        label = "ok" if verdict == "ok" else ("BLOCK" if s.get("blocking") else "warn")
        th = {k: s[k] for k in ("min", "no_significant_drop") if k in s}
        shown = "-" if value is None else round(value, 4)
        lines.append(f"  {sid:<38} {shown!s:<8} {json.dumps(th):<40} {note:<28} {label}")
    return blocked, lines


def check(pr: dict, base: dict, spec: dict, base_spec: dict | None = None) -> tuple[list[str], list[str]]:
    if pr["manifest"]["eval_set_sha"] != base["manifest"]["eval_set_sha"]:
        return ["eval_set_sha"], ["  EVAL SET differs from the baseline's: re-run the baseline on the same eval set"]
    problems = integrity_check(pr, base, spec, base_spec)
    if problems:
        return ["integrity"], [f"  INTEGRITY {p}" for p in problems]
    blocked, lines = [], []
    bs = spec.get("bootstrap", {})
    for m in spec["metrics"]:
        mid, th, val = m["id"], m.get("threshold", {}), pr["metrics"].get(m["id"])
        verdict, note = "ok", ""
        if val is None:
            verdict = "missing"
        elif "max" in th and val > th["max"]:
            verdict = "fail"
        elif "min" in th and val < th["min"]:
            verdict = "fail"
        elif "max_increase" in th and base["metrics"].get(mid):
            if val > base["metrics"][mid] * (1 + th["max_increase"]):
                verdict = "fail"
        if th.get("no_significant_drop") and val is not None:
            a, b = per_item(pr, mid), per_item(base, mid)
            keys = sorted(set(a) & set(b))
            d, lo, hi = stats.paired_bootstrap([a[k] for k in keys], [b[k] for k in keys],
                                               n_resamples=bs.get("n_resamples", 1000), rng=bs.get("rng", 2027))
            note = f"Δ {d:+.3f} CI [{lo:+.3f}, {hi:+.3f}]"
            if hi < 0:
                verdict = "fail"
        if verdict != "ok" and m.get("blocking"):
            blocked.append(mid)
        label = {"ok": "ok", "missing": "MISSING"}.get(verdict, "BLOCK" if m.get("blocking") else "warn")
        lines.append(f"  {mid:<22} {val!s:<8} {json.dumps(th):<40} {note:<28} {label}")
    if spec.get("slices"):
        sb, sl = slice_check(pr, base, spec)
        blocked += sb
        lines += sl
    return blocked, lines


def main(argv: list[str] | None = None) -> int:
    utf8_stdio()
    ap = argparse.ArgumentParser(prog="lab9.gate")
    ap.add_argument("--pr", required=True)
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--spec", default=str(SPEC))
    ap.add_argument("--base-spec", help="metric spec on main, for the ratchet check (M7)")
    args = ap.parse_args(argv)
    pr = json.loads(Path(args.pr).read_text(encoding="utf-8"))
    base = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    base_spec = load_spec(Path(args.base_spec)) if args.base_spec else None
    blocked, lines = check(pr, base, load_spec(Path(args.spec)), base_spec)
    print(f"gate: PR {pr['run_id']} vs main {base['run_id']} · eval set {pr['manifest']['eval_set_sha']}")
    print("\n".join(lines))
    if blocked:
        print(f"exit 1 — blocked: {', '.join(blocked)}")
        return 1
    print("exit 0 — merge")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
