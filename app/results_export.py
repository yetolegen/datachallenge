"""Organizers' results format 1.0 (CQA_Student_Guide_Appendix_Results.pdf):
results/dev/results.json and results/prod/results.json, one record per dialog of the set."""
import re

from app import config, db

# our checklist ids -> organizers' criterion codes; a swapped-in criterion keeps its own id
CODE = {"contact": "contact", "needs": "discovery", "identification": "identification",
        "consultation": "consultation", "empathy": "empathy", "dialog_management": "dialogue",
        "closing": "closure", "gross_errors": "critical"}


def turns(uids):
    # our uids are u1..uN in the original transcript order (client, bot and system included) = their numbering
    return [int(m.group(1)) for u in uids or [] if (m := re.fullmatch(r"u(\d+)", str(u)))]


def value_of(verdict, value):
    if verdict == "na":
        return None
    if verdict == "scale":
        return int(value) if float(value).is_integer() else value
    return verdict == "yes"


def record(conv, view):
    if not view:
        return {"conversation_id": conv["source_id"], "status": "error", "criteria": [], "score": None,
                "error": "Диалог не оценён."}
    pending = [it["criterion"]["title"] for it in view["items"]
               if not it.get("final") or it["final"].get("status") != "ok"]
    if pending:
        return {"conversation_id": conv["source_id"], "status": "error", "criteria": [], "score": None,
                "error": "Требует проверки: " + ", ".join(pending)}
    criteria = []
    for it in view["items"]:
        c, r, f = it["criterion"], it["result"], it["final"]
        ev = turns(r.get("evidence_ids"))
        if not ev and r.get("checked_range"):
            ev = turns([r["checked_range"].get("from"), r["checked_range"].get("to")])
        rationale = r.get("explanation") or ""
        if f.get("source") == "decision":
            dec = [a for a in it["appeals"] if a.get("action") == "changed"]
            if dec:
                rationale = f"Изменено проверяющим: {dec[-1]['reason']}"
        criteria.append({"criterion_id": CODE.get(c["id"], c["id"]),
                         "value": value_of(f["verdict"], f["value"]),
                         "rationale": rationale, "evidence_turns": ev})
    b = view["breakdown"]
    return {"conversation_id": conv["source_id"], "status": "ok", "criteria": criteria,
            "score": {"final_score": b["total"], "critical_flag": bool(b["critical_failed"])}}


def model_info(con, group):
    used = db.rows(con, "SELECT DISTINCT l.provider, l.model FROM llm_calls l JOIN conversations c"
                        " ON c.id = l.conversation_id WHERE c.dataset_group_id=? AND l.status='ok'", (group,))
    providers = {u["provider"] for u in used}
    mode = ("local" if providers <= {"ollama"} else "external_api" if providers <= {"openai", "anthropic"} else "hybrid") \
        if providers else "local"
    return {"mode": mode, "name": ", ".join(sorted({u["model"] for u in used})) or "unknown"}


def build(group):
    from app.main import evaluation_view, pick_evaluation  # shared view logic, no duplication
    with db.connect() as con:
        convs = db.rows(con, "SELECT * FROM conversations WHERE dataset_group_id=? ORDER BY source_id", (group,))
        if not convs:
            raise ValueError(f"Набор «{group}» не найден или пуст.")
        vc, results = {}, []
        for conv in convs:
            ev = pick_evaluation(con, conv["id"])
            results.append(record(conv, evaluation_view(con, ev, vc) if ev else None))
        model = model_info(con, group)
    ids = [r["conversation_id"] for r in results]
    assert len(ids) == len(set(ids)), "duplicate conversation_id in export"
    return {"schema_version": "1.0", "team_name": config.env("TEAM_NAME", "Avivengers"), "model": model,
            "results": results}
