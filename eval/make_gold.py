"""Draft gold labels from the current model results, for humans to correct.

python -m eval.make_gold                 # stratified pick: 6 RU, 5 KZ, 5 EN chats + 2 calls
python -m eval.make_gold --ids p1-ru-001,p1-kz-003

Then each annotator edits `value` (yes / no / na / scale number) and replaces annotator=model-draft
with their name. Rows still marked model-draft are ignored by run_eval.py.
"""
import argparse
import csv
import random

from app import checklist, db
from app.config import ROOT

OUT = ROOT / "eval" / "gold" / "gold_labels.csv"


def pick(con):
    rnd = random.Random(42)
    chats = db.rows(con, "SELECT id, language FROM conversations WHERE dataset_group_id='DEV' AND audio_url IS NULL ORDER BY id")
    calls = db.rows(con, "SELECT id FROM conversations WHERE dataset_group_id='DEV' AND audio_url IS NOT NULL ORDER BY id")
    ids = []
    for lang, n in (("RU", 6), ("KZ", 5), ("EN", 5)):
        pool = [c["id"] for c in chats if c["language"] == lang]
        ids += rnd.sample(pool, min(n, len(pool)))
    return ids + [c["id"] for c in calls[:2]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids")
    args = ap.parse_args()
    if OUT.exists():
        raise SystemExit(f"{OUT} already exists — move it away first, so human labels are never overwritten.")
    vid = checklist.active_version_id()
    criteria = checklist.get_version(vid)["checklist"]["criteria"]
    rows = []
    with db.connect() as con:
        ids = args.ids.split(",") if args.ids else pick(con)
        for cid in ids:
            res = {r["criterion_id"]: r for r in db.rows(
                con, "SELECT cr.* FROM criterion_results cr JOIN evaluations e ON e.id=cr.evaluation_id"
                     " WHERE e.conversation_id=? AND e.checklist_version_id=?", (cid, vid))}
            for c in criteria:
                r = res.get(c["id"])
                ok = r and r["status"] == "ok"
                val = (r["verdict"] if r["verdict"] != "scale" else int(r["value"])) if ok else ""
                rows.append([cid, c["id"], val, "model-draft", r["explanation"] if r else "нет результата"])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["conversation_id", "criterion_id", "value", "annotator", "note"])
        w.writerows(rows)
    print(f"wrote {len(rows)} rows for {len(ids)} dialogs -> {OUT}")


if __name__ == "__main__":
    main()
