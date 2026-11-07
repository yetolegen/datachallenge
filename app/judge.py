"""One LLM call per (conversation, criterion). Transcript first (shared prefix -> KV/prompt cache),
criterion block last. Evidence is cited by utterance id and checked here."""
import asyncio
import json
import re

from app import config, db
from app.checklist import criterion_hash, sha
from app.providers import complete

PROMPT_VERSION = "v1"
SYSTEM = (config.ROOT / "app" / "prompts" / "system.md").read_text(encoding="utf-8")
LANG_NAMES = {"ru": "Russian", "kk": "Kazakh", "en": "English"}
_sem = {"n": None, "sem": None}


def llm_settings():
    with db.connect() as con:
        over = db.get_state(con, "llm_settings", {}) or {}
    # empty is a real value for these (switch KZ routing / thinking off); for the rest it means "use default"
    can_be_empty = ("model_kz", "ollama_think", "ollama_think_kz", "provider_kz")
    return {**config.LLM_DEFAULTS,
            **{k: v for k, v in over.items() if v is not None and (v != "" or k in can_be_empty)}}


def settings_for(conv, s):
    """KZ dialogs go to the Kazakh model when one is set; the cache key follows the model."""
    if (conv.get("language") or "").upper() == "KZ" and s.get("model_kz"):
        return {**s, "provider": s.get("provider_kz") or s["provider"], "model": s["model_kz"],
                "ollama_think": s.get("ollama_think_kz", "")}
    return s


def semaphore(n):
    if _sem["n"] != n:
        _sem.update(n=n, sem=asyncio.Semaphore(n))
    return _sem["sem"]


def prices():
    p = config.ROOT / "config" / "prices.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def cost_of(model, tin, tout):
    pr = prices().get(model) or {}
    return (tin * pr.get("in", 0) + tout * pr.get("out", 0)) / 1_000_000


# ---------- prompt ----------

def transcript_block(conv, utts):
    lines = [f"[{u['uid']}] {u['raw_timestamp']} {u['speaker']}: {u['text']}" for u in utts]
    return (f"Канал: {conv['channel']}. Язык: {conv['language']}.\n"
            "<transcript>\n" + "\n".join(lines) + "\n</transcript>\n\n")


def focus_lines(c, utts):
    """Copies of the transcript lines a criterion depends on, so a small model doesn't get lost in
    a long dialog. Driven by the checklist's optional `focus` list; unknown keys are ignored."""
    picked = []
    for f in c.get("focus") or []:
        if f == "first_operator":
            picked += [u for u in utts if u["speaker"] == "operator"][:1]
        elif f == "last_turns":
            picked += utts[-4:]
        elif f == "system_events":
            picked += [u for u in utts if u["speaker"] == "system"]
    seen, out = set(), []
    for u in sorted(picked, key=lambda u: u["idx"]):
        if u["uid"] not in seen:
            seen.add(u["uid"])
            out.append(f"[{u['uid']}] {u['speaker']}: {u['text']}")
    return out


FOCUS_TITLES = {"first_operator": "первая реплика оператора", "last_turns": "конец диалога",
                "system_events": "системные события"}


def appeal_examples(cl, c, conv_id, k=3):
    """Reviewer corrections become examples ("learning from appeals"): latest k decisions that changed
    this criterion's AI answer, same criterion wording (hash), never from the dialog being judged."""
    if str(llm_settings().get("learn_from_appeals", "true")).lower() != "true":
        return []
    with db.connect() as con:
        decs = db.rows(con,
                       "SELECT d.new_verdict, d.new_value, d.reason, cr.evidence_ids, cr.checked_range, e.conversation_id"
                       " FROM decisions d JOIN appeals a ON a.id=d.appeal_id"
                       " JOIN criterion_results cr ON cr.id=a.criterion_result_id"
                       " JOIN evaluations e ON e.id=cr.evaluation_id"
                       " WHERE d.action='changed' AND cr.criterion_id=? AND cr.criterion_hash=? AND e.conversation_id<>?"
                       " ORDER BY d.id DESC LIMIT ?", (c["id"], criterion_hash(cl, c), conv_id or "", k))
        out = []
        for d in decs:
            uids = json.loads(d["evidence_ids"] or "[]")
            if not uids and d["checked_range"]:
                uids = list(json.loads(d["checked_range"]).values())
            utts = db.rows(con, f"SELECT uid, speaker, text FROM utterances WHERE conversation_id=? AND uid IN"
                                f" ({','.join('?' * len(uids))}) ORDER BY idx", (d["conversation_id"], *uids)) if uids else []
            answer = (str(int(d["new_value"])) if d["new_verdict"] == "scale" and d["new_value"] is not None
                      else d["new_verdict"])
            out.append({"source": d["conversation_id"], "lines": [f"[{u['uid']}] {u['speaker']}: {u['text']}" for u in utts][:4],
                        "answer": answer, "why": f"Проверяющий исправил оценку ИИ: {d['reason']}"})
    return out


def criterion_block(cl, c, utts=None, extra_examples=None):
    out = [f"Общие правила чек-листа:\n{cl.get('general_rules', '')}\n", f"КРИТЕРИЙ: {c['title']}"]
    lines = focus_lines(c, utts or [])
    if lines:
        what = ", ".join(FOCUS_TITLES[f] for f in c["focus"] if f in FOCUS_TITLES)
        out.append(f"Ключевые реплики для этого критерия ({what}), скопированы из транскрипта выше:\n"
                   + "\n".join(lines))
    elif c.get("focus") and utts is not None and "first_operator" in c["focus"]:
        out.append("Ключевые реплики: в транскрипте нет ни одной реплики оператора.")
    # worked examples from other labelled dialogs; never one from the dialog being judged (no leakage)
    this_conv = (utts or [{}])[0].get("conversation_id") if utts else None
    exs = [e for e in (c.get("examples") or []) + (extra_examples or []) if e.get("source") != this_conv]
    if exs:
        out.append("Примеры правильной оценки (из ДРУГИХ диалогов, только для ориентира):")
        for e in exs:
            out.append("\n".join(e.get("lines") or []) + f"\n→ ответ: {e.get('answer')}. {e.get('why', '')}")
    if c.get("instructions"):
        out.append(f"Пояснение: {c['instructions']}")
    if c["answer_type"] == "binary":
        if c.get("is_critical"):
            out.append("Это флаг грубой ошибки: verdict \"yes\" = нарушение выявлено, \"no\" = не выявлено.")
        out.append(f"verdict \"yes\" (Да): {c['yes_rule']}")
        out.append(f"verdict \"no\" (Нет): {c['no_rule']}")
    else:
        out.append("verdict \"scale\" и value — ровно одна из ступеней:")
        out += [f"  {s['value']}: {s['label']}" for s in sorted(c["scale"]["steps"], key=lambda s: -s["value"])]
    if c.get("na_allowed"):
        out.append(f"verdict \"na\" (N/A) только если: {c['na_rule']} Тогда заполни na_reason.")
    else:
        out.append("N/A для этого критерия не допускается.")
    out.append("Верни JSON: reasoning, verdict, " + ("value, " if c["answer_type"] == "scale" else "") +
               "evidence_ids, checked_range {from,to}, na_reason, explanation, confidence (high|medium|low).")
    return "\n".join(out)


def verdicts(c):
    v = ["scale"] if c["answer_type"] == "scale" else ["yes", "no"]
    return v + (["na"] if c.get("na_allowed") else [])


def schema_for(c, uids):
    props = {"reasoning": {"type": "string"},
             "verdict": {"type": "string", "enum": verdicts(c)}}
    if c["answer_type"] == "scale":
        props["value"] = {"type": "integer", "enum": [int(s["value"]) for s in c["scale"]["steps"]]}
    props.update({
        "evidence_ids": {"type": "array", "items": {"type": "string", "enum": uids}},
        "checked_range": {"type": "object", "properties": {"from": {"type": "string", "enum": uids},
                                                           "to": {"type": "string", "enum": uids}},
                          "required": ["from", "to"]},
        "na_reason": {"type": "string"},
        "explanation": {"type": "string"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]}})
    return {"type": "object", "properties": props, "required": list(props)}


# ---------- parsing / validation ----------

def parse_json(text):
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise ValueError("ответ не содержит JSON")
        return json.loads(m.group(0))


def validate(out, c, utts):
    """mutates out into clean form, returns list of problems (empty = ok)"""
    if not isinstance(out, dict):
        return ["ответ не является JSON-объектом"]
    errs = []
    by = {u["uid"]: u for u in utts}
    v = out.get("verdict")
    if v not in verdicts(c):
        errs.append(f"verdict должен быть одним из {verdicts(c)}")
    if v == "scale":
        steps = [float(s["value"]) for s in c["scale"]["steps"]]
        try:
            val = float(out.get("value"))
        except (TypeError, ValueError):
            val = None
        if val not in steps:
            errs.append(f"value должен быть одной из ступеней {[int(s) for s in steps]}")
        out["value"] = val
    else:
        out["value"] = None
    ev = out.get("evidence_ids") if isinstance(out.get("evidence_ids"), list) else []
    bad = [e for e in ev if e not in by]
    if bad:
        errs.append(f"несуществующие evidence_ids: {bad}")
    out["evidence_ids"] = [e for e in ev if e in by]
    if v == "na" and not str(out.get("na_reason") or "").strip():
        errs.append("для na нужен na_reason")
    if v == "yes" and not out["evidence_ids"]:
        errs.append("для yes нужна хотя бы одна реплика-доказательство")
    if v == "yes" and c.get("evidence_speaker") == "operator" and out["evidence_ids"] \
            and not any(by[e]["speaker"] == "operator" for e in out["evidence_ids"]):
        errs.append("для yes нужна реплика оператора в evidence_ids")
    cr = out.get("checked_range")
    if not isinstance(cr, dict) or cr.get("from") not in by or cr.get("to") not in by:
        errs.append("checked_range должен ссылаться на существующие реплики")
        out["checked_range"] = None
    elif by[cr["from"]]["idx"] > by[cr["to"]]["idx"]:
        out["checked_range"] = {"from": cr["to"], "to": cr["from"]}
    if not str(out.get("explanation") or "").strip():
        errs.append("explanation пустой")
    if out.get("confidence") not in ("high", "medium", "low"):
        out["confidence"] = "medium"
    return errs


# ---------- one criterion ----------

def load_conversation(con, conv_id):
    conv = db.row(con, "SELECT * FROM conversations WHERE id=?", (conv_id,))
    utts = db.rows(con, "SELECT * FROM utterances WHERE conversation_id=? ORDER BY idx", (conv_id,))
    return conv, utts


def cache_key(conv, cl, c, s, extra=None):
    parts = [conv["conversation_hash"], criterion_hash(cl, c), PROMPT_VERSION, s["provider"], s["model"]]
    if extra:  # appeal examples change the prompt, so they're part of the key (absent = old keys stay valid)
        parts.append(extra)
    return sha(parts)


async def judge_criterion(conv, utts, cl, c, s, extra=None):
    """-> (result dict, cache_hit). Model problems never raise: they end as status needs_review."""
    key = cache_key(conv, cl, c, s, extra)
    with db.connect() as con:
        hit = db.row(con, "SELECT result_json FROM judge_cache WHERE cache_key=?", (key,))
    if hit:
        return json.loads(hit["result_json"]), True

    system = SYSTEM.replace("{lang}", LANG_NAMES.get(config.EXPLANATION_LANG, "Russian"))
    user = transcript_block(conv, utts) + criterion_block(cl, c, utts, extra)
    schema = schema_for(c, [u["uid"] for u in utts])
    meta = {"criterion": c, "utterances": utts}
    raw_outputs, total_cost, errs, out = [], 0.0, ["нет ответа"], {}
    for attempt in range(2):
        prompt = user if attempt == 0 else (
            user + "\n\nПредыдущий ответ не прошёл проверку: " + "; ".join(errs) + ". Исправь и верни JSON заново.")
        status, err, res = "ok", None, None
        async with semaphore(int(s["concurrency"])):
            try:
                res = await complete(s, system, prompt, schema, meta)
            except Exception as e:  # noqa: BLE001 - any provider failure becomes needs_review, never a crash
                status, err = "error", str(e)[:500]
        if res:
            raw_outputs.append(res.text)
            try:
                out = parse_json(res.text)
                errs = validate(out, c, utts)
            except (ValueError, json.JSONDecodeError) as e:
                errs = [f"не удалось разобрать JSON: {e}"]
            if errs:
                status, err = "invalid", "; ".join(errs)
            total_cost += cost_of(res.model, res.in_tokens, res.out_tokens)
        else:
            errs = [err]
        with db.connect() as con:
            con.execute("INSERT INTO llm_calls(cache_key,conversation_id,criterion_id,provider,model,prompt_version,"
                        "in_tokens,out_tokens,latency_ms,cost,status,error,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (key, conv["id"], c["id"], s["provider"], s["model"], PROMPT_VERSION,
                         res.in_tokens if res else 0, res.out_tokens if res else 0, res.latency_ms if res else 0,
                         cost_of(res.model, res.in_tokens, res.out_tokens) if res else 0, status, err, db.now()))
        if not errs or status == "error":
            break  # provider down: retrying with the error text won't help

    result = {"criterion_hash": criterion_hash(cl, c), "cache_key": key, "model": s["model"],
              "prompt_version": PROMPT_VERSION, "cost": total_cost, "raw_output": "\n---\n".join(raw_outputs)}
    if errs:
        result.update(status="needs_review", verdict=None, value=None, evidence_ids=[], checked_range=None,
                      na_reason="", explanation="Требует проверки: " + "; ".join(errs)[:400],
                      reasoning=out.get("reasoning", "") if isinstance(out, dict) else "", confidence="low")
        return result, False
    result.update(status="ok", verdict=out["verdict"], value=out["value"], evidence_ids=out["evidence_ids"],
                  checked_range=out["checked_range"], na_reason=out.get("na_reason") or "",
                  explanation=out["explanation"], reasoning=out.get("reasoning", ""), confidence=out["confidence"])
    with db.connect() as con:
        con.execute("INSERT OR REPLACE INTO judge_cache(cache_key,result_json,created_at) VALUES(?,?,?)",
                    (key, json.dumps(result, ensure_ascii=False), db.now()))
    return result, False


# ---------- whole conversation ----------

def save_result(con, eval_id, cid, r):
    # upsert keeps the row id, so appeals stay attached when a result is refreshed
    con.execute(
        "INSERT INTO criterion_results(evaluation_id,criterion_id,criterion_hash,verdict,value,evidence_ids,checked_range,"
        "na_reason,critical_violation,explanation,reasoning,confidence,status,cache_key,raw_output,model,prompt_version,cost)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(evaluation_id,criterion_id) DO UPDATE SET"
        " criterion_hash=excluded.criterion_hash, verdict=excluded.verdict, value=excluded.value,"
        " evidence_ids=excluded.evidence_ids, checked_range=excluded.checked_range, na_reason=excluded.na_reason,"
        " critical_violation=excluded.critical_violation, explanation=excluded.explanation, reasoning=excluded.reasoning,"
        " confidence=excluded.confidence, status=excluded.status, cache_key=excluded.cache_key,"
        " raw_output=excluded.raw_output, model=excluded.model, prompt_version=excluded.prompt_version, cost=excluded.cost",
        (eval_id, cid, r["criterion_hash"], r["verdict"], r["value"], json.dumps(r["evidence_ids"]),
         json.dumps(r["checked_range"]), r["na_reason"], 0, r["explanation"], r["reasoning"], r["confidence"],
         r["status"], r["cache_key"], r["raw_output"], r["model"], r["prompt_version"], r["cost"]))


async def evaluate_conversation(conv_id, version, run_id=None, on_result=None):
    """version: dict from checklist.get_version. on_result(cache_hit, status) reports progress."""
    cl, s = version["checklist"], llm_settings()
    with db.connect() as con:
        conv, utts = load_conversation(con, conv_id)
        if not conv:
            raise ValueError(f"диалог {conv_id} не найден")
        s = settings_for(conv, s)
        con.execute("INSERT INTO evaluations(conversation_id,checklist_version_id,run_id,status,created_at)"
                    " VALUES(?,?,?,?,?) ON CONFLICT(conversation_id,checklist_version_id) DO UPDATE SET"
                    " run_id=excluded.run_id, status='running'", (conv_id, version["id"], run_id, "running", db.now()))
        eval_id = db.row(con, "SELECT id FROM evaluations WHERE conversation_id=? AND checklist_version_id=?",
                         (conv_id, version["id"]))["id"]
        existing = {r["criterion_id"]: r["cache_key"] for r in db.rows(
            con, "SELECT criterion_id, cache_key FROM criterion_results WHERE evaluation_id=? AND status='ok'", (eval_id,))}

    async def one(c):
        extra = appeal_examples(cl, c, conv_id)
        if existing.get(c["id"]) == cache_key(conv, cl, c, s, extra):
            if on_result:
                on_result(True, "ok")
            return
        r, hit = await judge_criterion(conv, utts, cl, c, s, extra)
        with db.connect() as con:
            save_result(con, eval_id, c["id"], r)
        if on_result:
            on_result(hit, r["status"])

    await asyncio.gather(*(one(c) for c in cl["criteria"]))
    with db.connect() as con:
        nr = db.row(con, "SELECT COUNT(*) n FROM criterion_results WHERE evaluation_id=? AND status='needs_review'",
                    (eval_id,))["n"]
        con.execute("UPDATE evaluations SET status=? WHERE id=?", ("needs_review" if nr else "done", eval_id))
    return eval_id
