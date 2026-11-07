"""Write the organizers' results file (format 1.0) for a dataset.

python -m eval.export_results --group DEV                           -> results/dev/results.json
python -m eval.export_results --group <control group_id> --expect 10 -> results/prod/results.json

Run it on the machine whose DB has the evaluations, then commit + push results/ before stop-code.
"""
import argparse
import json
import sys

from app.config import ROOT
from app.results_export import build

CODES = {"contact", "discovery", "identification", "consultation", "empathy", "dialogue", "closure", "critical"}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--group", required=True)
    ap.add_argument("--out", help="default: results/dev or results/prod")
    ap.add_argument("--expect", type=int, help="expected number of dialogs (prod: 10)")
    a = ap.parse_args()
    data = build(a.group)
    out = a.out or ROOT / "results" / ("dev" if a.group.upper() == "DEV" else "prod") / "results.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    res = data["results"]
    ok = [r for r in res if r["status"] == "ok"]
    print(f"{out}: {len(res)} dialogs, ok {len(ok)}, error {len(res) - len(ok)}; model {data['model']}")
    for r in res:
        if r["status"] != "ok":
            print(f"  ERROR {r['conversation_id']}: {r['error']}")
        elif {c['criterion_id'] for c in r["criteria"]} != CODES:
            print(f"  NOTE {r['conversation_id']}: criteria {sorted(c['criterion_id'] for c in r['criteria'])}"
                  " differ from the 8 official codes (expected after a criterion swap)")
    if a.expect and len(res) != a.expect:
        print(f"!! expected {a.expect} dialogs, got {len(res)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
