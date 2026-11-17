"""Extra variants beyond the public ones (rare / quiet_override / misreport / paraphrase), run through the gate.

    cd src && PYTHONPATH=. python scripts/extra_variants.py

Backs the sample-size notes in docs/eval_card.md. Needs reports/baseline_v2.json."""
import json
from pathlib import Path
from lab8.shapes import SHAPES
from lab9 import gate, variants
from lab9.eval import load_spec, run_eval
from lab9.variant_agent import make_shape
variants.register()
spec=load_spec(Path("config/metric_spec_v2.yaml")); base=json.load(open("reports/baseline_v2.json",encoding="utf-8"))
for name,bugs in {"rare_sum":{"no_override","rare","sum_parts"},"hidden_quiet":{"quiet_override"},"hidden_misreport":{"no_override","misreport"},"hidden_leak_odd":{"no_override","intermittent","leaky_trace"},"hidden_refactor":{"no_override","paraphrase"}}.items():
    SHAPES.setdefault(name, make_shape(name,bugs))
    rep=run_eval(name,spec); blocked,_=gate.check(rep,base,spec)
    print(f"{name:<18} {'BLOCK' if blocked else 'merge'} {blocked} pass_hat_5={rep['metrics']['pass_hat_5']} pass_rate={rep['metrics']['pass_rate']}")
