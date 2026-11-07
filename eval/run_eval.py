"""Compare model results in the DB with human gold labels.

python -m eval.run_eval [--gold eval/gold/gold_labels.csv] [--version <checklist_version_id>]

Run the model on the gold dialogs first (UI: «Оценить» on DEV). Writes eval/reports/<timestamp>.md:
per-criterion accuracy + Cohen's kappa, scale MAE, per-language accuracy, total-score error and
the disagreement list with deep links.
"""
import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime

from app import checklist, db, scoring
from app.config import ROOT


def kappa(pairs):
    n = len(pairs)
    if not n:
        return 0.0
    po = sum(a == b for a, b in pairs) / n
    ca, cb = Counter(a for a, _ in pairs), Counter(b for _, b in pairs)
    pe = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / (n * n)
    return 1.0 if pe == 1 else (po - pe) / (1 - pe)


def norm(v):
    v = str(v).strip().lower()
    return v if v in ("yes", "no", "na") else str(int(float(v)))


def as_result(v):
    v = norm(v)
    return {"verdict": v, "value": None, "status": "ok"} if v in ("yes", "no", "na") else \
        {"verdict": "scale", "value": float(v), "status": "ok"}


def main():
    sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252 and choke on Cyrillic
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", default=str(ROOT / "eval" / "gold" / "gold_labels.csv"))
    ap.add_argument("--version", type=int)
    args = ap.parse_args()
    vid = args.version or checklist.active_version_id()
    version = checklist.get_version(vid)
    cl = version["checklist"]
    crit = {c["id"]: c for c in cl["criteria"]}

    gold = defaultdict(dict)
    with open(args.gold, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r["annotator"].strip() not in ("", "model-draft") and r["value"].strip() and r["criterion_id"] in crit:
                gold[r["conversation_id"]][r["criterion_id"]] = norm(r["value"])
    if not gold:
        raise SystemExit("No human-labelled rows (annotator != model-draft) in the gold file.")

    per_crit, per_lang, total_err, disagreements, missing = defaultdict(list), defaultdict(list), [], [], 0
    with db.connect() as con:
        for cid, labels in gold.items():
            conv = db.row(con, "SELECT language FROM conversations WHERE id=?", (cid,))
            model = {r["criterion_id"]: r for r in db.rows(
                con, "SELECT cr.* FROM criterion_results cr JOIN evaluations e ON e.id=cr.evaluation_id"
                     " WHERE e.conversation_id=? AND e.checklist_version_id=?", (cid, vid))}
            for k, g in labels.items():
                r = model.get(k)
                if not r or r["status"] != "ok":
                    missing += 1
                    continue
                m = norm(r["verdict"] if r["verdict"] != "scale" else r["value"])
                per_crit[k].append((g, m))
                per_lang[conv["language"] if conv else "?"].append(g == m)
                if g != m:
                    disagreements.append((cid, k, g, m, r["explanation"], json.loads(r["evidence_ids"] or "[]")))
            if set(labels) >= set(crit):  # fully labelled dialog -> compare totals too
                gt = scoring.score(cl, {k: as_result(v) for k, v in labels.items()})["total"]
                mt = scoring.score(cl, {k: {"verdict": r["verdict"], "value": r["value"], "status": r["status"]}
                                        for k, r in model.items()})["total"]
                if gt is not None and mt is not None:
                    total_err.append(abs(gt - mt))

    allp = [p for v in per_crit.values() for p in v]
    acc = sum(a == b for a, b in allp) / max(1, len(allp))
    lines = [f"# Eval report {datetime.now():%Y-%m-%d %H:%M}", "",
             f"Checklist v{version['version']}, dialogs: {len(gold)}, labels compared: {len(allp)}, missing/needs_review: {missing}",
             "", f"**Overall accuracy: {acc:.1%}**, kappa {kappa(allp):.3f}"
             + (f", total score MAE {sum(total_err) / len(total_err):.2f} pts over {len(total_err)} fully labelled dialogs"
                if total_err else ""),
             "", "| Criterion | n | Accuracy | Kappa | Scale MAE |", "|---|---|---|---|---|"]
    for k, pairs in per_crit.items():
        mae = ""
        if crit[k]["answer_type"] == "scale":
            num = [(float(a), float(b)) for a, b in pairs if "na" not in (a, b)]
            mae = f"{sum(abs(a - b) for a, b in num) / len(num):.2f}" if num else ""
        lines.append(f"| {crit[k]['title']} | {len(pairs)} | {sum(a == b for a, b in pairs) / len(pairs):.1%}"
                     f" | {kappa(pairs):.3f} | {mae} |")
    lines += ["", "| Language | Accuracy |", "|---|---|"]
    lines += [f"| {lang} | {sum(v) / len(v):.1%} (n={len(v)}) |" for lang, v in sorted(per_lang.items())]
    lines += ["", "## Disagreements (gold vs model)", ""]
    lines += [f"- `{cid}` {crit[k]['title']}: gold **{g}**, model **{m}** — {expl} "
              f"([open](http://localhost:8000/#/dialog/{cid}?u={(ev or ['u1'])[0]}))"
              for cid, k, g, m, expl, ev in disagreements]
    report = "\n".join(lines)
    out = ROOT / "eval" / "reports" / f"{datetime.now():%Y%m%d-%H%M}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    print(report)
    print(f"\nsaved {out}")


if __name__ == "__main__":
    main()
