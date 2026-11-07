"""Build the SFT dataset for the RU/EN judge from gold + silver labels.

python -m eval.make_sft [--val 3]

Inputs are built exactly like the app (judge.SYSTEM + transcript_block + criterion_block) from the ACTIVE
checklist, so the model learns to follow the rubric text rather than memorise labels. Writes
eval/sft/train.jsonl and eval/sft/val.jsonl (chat format) plus eval/sft/stats.json.

Never used for training: HOLDOUT dialogs, KZ dialogs (they stay on sherkala), calls (p4-*), and any
checklist example quoted from a HOLDOUT dialog.
"""
import argparse
import csv
import json
import random
import sys
from collections import Counter

from app import checklist, config, db, judge
from app.config import ROOT

HOLDOUT = {"p1-ru-001", "p1-ru-013", "p1-ru-028", "p1-kz-007", "p1-en-011", "p1-en-015", "p1-kz-014", "p1-ru-024"}
GOLD = ROOT / "eval" / "gold" / "gold_labels.csv"
SILVER = ROOT / "eval" / "gold" / "silver_labels.csv"
OUT = ROOT / "eval" / "sft"

# Short targets for labels the app got wrong (or never judged). 1–2 sentences, in the app's explanation style.
TEMPLATES = {
    ("contact", "yes"): "В первой реплике оператора есть приветствие и имя.",
    ("contact", "no"): "В первой реплике оператора нет приветствия или имени.",
    ("needs", "yes"): "До ответа по существу оператор уточнил то, чего клиент ещё не сообщил.",
    ("needs", "no"): "Оператор ответил, не уточнив недостающие детали, или переспросил уже известное.",
    ("needs", "na"): "Оператор ничего не уточнял, и известных деталей хватало для правильного ответа.",
    ("identification", "yes"): "Оператор работал с данными счёта после подтверждения личности.",
    ("identification", "no"): "Оператор сообщил данные счёта или действовал со счётом без подтверждения личности.",
    ("identification", "na"): "Оператор не раскрывал данные счёта и не выполнял действий со счётом.",
    ("consultation", "na"): "Чат закончился до того, как клиент сформулировал запрос.",
    ("empathy", "yes"): "Оператор вежлив и отреагировал на проблему клиента.",
    ("empathy", "no"): "Оператор был невежлив или не отреагировал на описанную клиентом проблему.",
    ("closing", "yes"): "В конце есть итог или предложение помощи и прощание.",
    ("closing", "no"): "В конце нет итога и предложения помощи, есть только прощание или его нет вовсе.",
    ("closing", "na"): "Чат оборвался до того, как оператор мог его завершить.",
    ("gross_errors", "yes"): "Выявлена грубая ошибка: оскорбление, услуга в обход компании или раскрытие данных.",
    ("gross_errors", "no"): "Оскорблений, услуг в обход компании и раскрытия данных нет.",
}
STEP_TEXT = "Оценка по шкале: {v} — {label}"


def load_labels():
    out, src = {}, {}
    for path, name in ((GOLD, "gold"), (SILVER, "silver")):
        if not path.exists():
            continue
        with open(path, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                if r["annotator"].strip() in ("", "model-draft") or not r["value"].strip():
                    continue
                cid = r["conversation_id"]
                if name == "silver" and cid in src and src[cid] == "gold":
                    continue  # gold wins
                out.setdefault(cid, {})[r["criterion_id"]] = r["value"].strip().lower()
                src[cid] = name
    return out, src


def label_to_verdict(c, v):
    if v == "na":
        return "na", None
    if c["answer_type"] == "scale":
        return "scale", int(float(v))
    return v, None


def app_result(con, conv_id, cid, verdict, value):
    """Newest stored app result for this criterion whose answer matches the label (for its evidence/text)."""
    rows = db.rows(con, "SELECT cr.* FROM criterion_results cr JOIN evaluations e ON e.id=cr.evaluation_id"
                        " WHERE e.conversation_id=? AND cr.criterion_id=? AND cr.status='ok'"
                        " ORDER BY e.checklist_version_id DESC", (conv_id, cid))
    for r in rows:
        if r["verdict"] == verdict and (verdict != "scale" or int(r["value"]) == value):
            return r
    return None


def fallback_evidence(c, utts, verdict):
    ops = [u for u in utts if u["speaker"] == "operator"]
    focus = c.get("focus") or []
    if "first_operator" in focus and ops:
        return [ops[0]["uid"]]
    if "last_turns" in focus:
        tail = [u for u in utts[-4:] if u["speaker"] == "operator"] or utts[-1:]
        return [tail[-1]["uid"]]
    if "system_events" in focus:
        sys_ev = [u for u in utts if u["speaker"] == "system" and u["idx"] > (ops[0]["idx"] if ops else 0)]
        return [u["uid"] for u in (sys_ev[:1] + ops[:1])] or [utts[0]["uid"]]
    if c["id"] == "needs":
        first_client = [u for u in utts if u["speaker"] == "client"][:1]
        return [u["uid"] for u in first_client + ops[:1]]
    return [ops[0]["uid"]] if ops else [utts[0]["uid"]]


def first_sentences(text, n=2):
    parts = [p.strip() for p in (text or "").replace("\n", " ").split(". ") if p.strip()]
    s = ". ".join(parts[:n])
    return s if s.endswith(".") else s + "."


def target(c, utts, label, app):
    verdict, value = label_to_verdict(c, label)
    if app:
        ev = json.loads(app["evidence_ids"] or "[]")
        cr = json.loads(app["checked_range"]) if app["checked_range"] else None
        reasoning = first_sentences(app["reasoning"] or app["explanation"])
        explanation = app["explanation"]
        na_reason = app["na_reason"] or ""
    else:
        ev = fallback_evidence(c, utts, verdict)
        cr = None
        if verdict == "scale":
            step = next(s for s in c["scale"]["steps"] if int(s["value"]) == value)
            reasoning = STEP_TEXT.format(v=value, label=step["label"])
        else:
            reasoning = TEMPLATES.get((c["id"], verdict), "")
        explanation = f"{c['title']} — {'N/A' if verdict == 'na' else {'yes': 'Да', 'no': 'Нет'}.get(verdict, value)}. {reasoning}"
        na_reason = reasoning if verdict == "na" else ""
    by = {u["uid"]: u for u in utts}
    ev = [e for e in ev if e in by]
    if verdict == "yes" and c.get("evidence_speaker") == "operator" and not any(by[e]["speaker"] == "operator" for e in ev):
        ev += [u["uid"] for u in utts if u["speaker"] == "operator"][:1]
    if not cr or cr.get("from") not in by or cr.get("to") not in by:
        cr = {"from": utts[0]["uid"], "to": utts[-1]["uid"]}
    out = {"reasoning": reasoning, "verdict": verdict}
    if c["answer_type"] == "scale":
        # schema requires an int step even for N/A; the app ignores it then
        out["value"] = value if value is not None else min(int(s["value"]) for s in c["scale"]["steps"])
    out.update({"evidence_ids": ev, "checked_range": cr, "na_reason": na_reason if verdict == "na" else "",
                "explanation": explanation, "confidence": "high"})
    return out


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--val", type=int, default=3, help="TRAIN dialogs kept aside for eval loss")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    version = checklist.get_version(checklist.active_version_id())
    cl = json.loads(json.dumps(version["checklist"]))
    leaked = []
    for c in cl["criteria"]:  # holdout text must never reach training inputs
        keep = [e for e in c.get("examples") or [] if e.get("source") not in HOLDOUT]
        leaked += [(c["id"], e["source"]) for e in c.get("examples") or [] if e.get("source") in HOLDOUT]
        c["examples"] = keep
    crit = {c["id"]: c for c in cl["criteria"]}
    system = judge.SYSTEM.replace("{lang}", judge.LANG_NAMES.get(config.EXPLANATION_LANG, "Russian"))

    labels, src = load_labels()
    ids = sorted(cid for cid in labels if cid not in HOLDOUT and "-kz-" not in cid and cid.startswith("p1-"))
    random.Random(args.seed).shuffle(ids)
    val_ids = set(ids[:args.val])

    OUT.mkdir(parents=True, exist_ok=True)
    stats = Counter()
    files = {"train": open(OUT / "train.jsonl", "w", encoding="utf-8"), "val": open(OUT / "val.jsonl", "w", encoding="utf-8")}
    with db.connect() as con:
        for cid in sorted(ids):
            conv, utts = judge.load_conversation(con, cid)
            if not conv:
                print("not in DB, skipped:", cid)
                continue
            split = "val" if cid in val_ids else "train"
            for kid, val in labels[cid].items():
                c = crit.get(kid)
                if not c:
                    continue
                verdict, value = label_to_verdict(c, val)
                app = app_result(con, cid, kid, verdict, value)
                user = judge.transcript_block(conv, utts) + judge.criterion_block(cl, c, utts)
                rec = {"id": f"{cid}/{kid}", "messages": [
                    {"role": "system", "content": system}, {"role": "user", "content": user},
                    {"role": "assistant", "content": json.dumps(target(c, utts, val, app), ensure_ascii=False)}]}
                files[split].write(json.dumps(rec, ensure_ascii=False) + "\n")
                stats[split] += 1
                stats[f"{split}_{src[cid]}"] += 1
                stats["target_from_app" if app else "target_from_template"] += 1
    for f in files.values():
        f.close()
    info = {"checklist_version_id": version["id"], "checklist_version": version["version"],
            "train_dialogs": len(ids) - len(val_ids), "val_dialogs": sorted(val_ids), "holdout": sorted(HOLDOUT),
            "dropped_holdout_examples": leaked, **stats}
    (OUT / "stats.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(info, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
