import csv
import html
import io
import json
import logging
import re
import statistics
from contextlib import asynccontextmanager

import httpx
from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from app import checklist as cl_mod
from app import config, db, ingest, judge, runs, scoring

logging.basicConfig(level=logging.INFO,
                    format='{"t":"%(asctime)s","lvl":"%(levelname)s","log":"%(name)s","msg":"%(message)s"}')


@asynccontextmanager
async def lifespan(app):
    db.init()
    cl_mod.ensure_seed()
    runs.mark_interrupted()
    yield


app = FastAPI(title="CQA", lifespan=lifespan)
if config.CORS_ORIGINS:
    app.add_middleware(CORSMiddleware, allow_origins=config.CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"])


@app.exception_handler(ValueError)
async def value_error(_, e):
    return JSONResponse({"error": "Некорректный запрос", "detail": str(e)}, status_code=400)


@app.exception_handler(HTTPException)
async def http_error(_, e):
    return JSONResponse({"error": str(e.detail), "detail": str(e.detail)}, status_code=e.status_code)


@app.exception_handler(Exception)
async def any_error(_, e):
    logging.getLogger("cqa").exception("unhandled")
    return JSONResponse({"error": "Внутренняя ошибка сервера", "detail": str(e)[:300]}, status_code=500)


def not_found(what):
    raise HTTPException(404, f"{what}: не найдено.")


def J(v, default=None):
    return json.loads(v) if v else default


# ---------- health / settings ----------

SETTING_KEYS = ["provider", "model", "ollama_url", "ollama_num_ctx", "ollama_think", "openai_base_url",
                "concurrency", "timeout_s", "model_kz", "provider_kz", "ollama_think_kz"]


def public_settings():
    s = judge.llm_settings()
    return {**{k: s[k] for k in SETTING_KEYS}, "openai_key_set": bool(config.OPENAI_API_KEY),
            "api_template_set": "{group_id}" in config.API_URL_TEMPLATE, "prompt_version": judge.PROMPT_VERSION}


@app.get("/api/health")
def health():
    return {"ok": True, "llm": public_settings()}


@app.get("/api/settings")
def get_settings():
    return public_settings()


@app.put("/api/settings")
def put_settings(body: dict = Body(...)):
    # keep empty strings: clearing model_kz must switch routing off
    clean = {k: str(body[k]).strip() for k in SETTING_KEYS if body.get(k) is not None}
    for k in ("provider", "provider_kz"):
        if clean.get(k) and clean[k] not in ("ollama", "openai", "fake"):
            raise ValueError("Провайдер: ollama, openai или fake.")
    for k in ("concurrency", "ollama_num_ctx", "timeout_s"):
        if clean.get(k) and (not clean[k].isdigit() or int(clean[k]) < 1):
            raise ValueError(f"{k} должно быть целым числом больше 0.")
    with db.connect() as con:
        cur = db.get_state(con, "llm_settings", {}) or {}
        db.set_state(con, "llm_settings", {**cur, **clean})
    return public_settings()


@app.get("/api/llm/models")
async def llm_models():
    """Connection check for every provider in use; also says if a configured model isn't installed."""
    s = judge.llm_settings()
    used = {s["provider"]} | ({s.get("provider_kz") or s["provider"]} if s.get("model_kz") else set())
    out = {"ok": True, "models": [], "problems": []}
    async with httpx.AsyncClient(timeout=15) as cli:
        if "ollama" in used:
            try:
                r = await cli.get(s["ollama_url"].rstrip("/") + "/api/tags")
                names = [m["name"] for m in r.json().get("models", [])]
                out["models"] += names
                wanted = [m for p, m in ((s["provider"], s["model"]),
                                         (s.get("provider_kz") or s["provider"], s.get("model_kz"))) if p == "ollama" and m]
                for m in wanted:
                    if not any(n == m or n.split(":")[0] == m for n in names):
                        out["problems"].append(f"Модель «{m}» не установлена в Ollama (ollama pull / ollama create).")
            except Exception as e:  # noqa: BLE001
                out["problems"].append(f"Ollama недоступна по адресу {s['ollama_url']}: {e}")
        if "openai" in used:
            if not config.OPENAI_API_KEY:
                out["problems"].append("Облако: OPENAI_API_KEY не задан в .env.")
            else:
                try:
                    r = await cli.get(s["openai_base_url"].rstrip("/") + "/models",
                                      headers={"Authorization": f"Bearer {config.OPENAI_API_KEY}"})
                    if r.status_code >= 400:
                        out["problems"].append(f"Облако ответило {r.status_code}: {r.text[:200]}")
                    else:
                        out["models"] += [m.get("id", "") for m in r.json().get("data", [])][:200]
                except Exception as e:  # noqa: BLE001
                    out["problems"].append(f"Облако недоступно ({s['openai_base_url']}): {e}")
    out["ok"] = not out["problems"]
    out["detail"] = "; ".join(out["problems"])
    return out


# ---------- datasets ----------

@app.post("/api/datasets/import")
def import_dataset(body: dict = Body(...)):
    return ingest.fetch_group(str(body.get("group_id") or ""))


@app.post("/api/datasets/upload")
async def upload_dataset(file: UploadFile = File(...), group_id: str = Form("")):
    data = await file.read(config.MAX_UPLOAD_BYTES + 1)
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise ValueError("Файл больше 20 МБ.")
    try:
        return ingest.import_payload(data, group_id or None)
    except json.JSONDecodeError as e:
        raise ValueError(f"Файл не является корректным JSON: {e}")


@app.get("/api/datasets")
def list_datasets():
    with db.connect() as con:
        return db.rows(con, "SELECT d.group_id, d.imported_at,"
                            " (SELECT COUNT(*) FROM conversations c WHERE c.dataset_group_id=d.group_id) AS conversations"
                            " FROM datasets d ORDER BY d.imported_at DESC")


# ---------- evaluation view ----------

def pick_evaluation(con, conv_id, eval_id=None):
    if eval_id:
        return db.row(con, "SELECT * FROM evaluations WHERE id=? AND conversation_id=?", (eval_id, conv_id))
    active = db.get_state(con, "active_checklist_version_id")
    return (db.row(con, "SELECT * FROM evaluations WHERE conversation_id=? AND checklist_version_id=?", (conv_id, active))
            or db.row(con, "SELECT * FROM evaluations WHERE conversation_id=? ORDER BY checklist_version_id DESC LIMIT 1",
                      (conv_id,)))


def evaluation_view(con, ev, vcache=None):
    vcache = {} if vcache is None else vcache
    vid = ev["checklist_version_id"]
    if vid not in vcache:
        vcache[vid] = cl_mod.get_version(vid)
    version = vcache[vid]
    cl = version["checklist"]
    results = {r["criterion_id"]: r for r in
               db.rows(con, "SELECT * FROM criterion_results WHERE evaluation_id=?", (ev["id"],))}
    appeals = db.rows(con, "SELECT a.*, d.action, d.new_verdict, d.new_value, d.reason, d.created_at AS decided_at"
                           " FROM appeals a LEFT JOIN decisions d ON d.appeal_id=a.id"
                           " WHERE a.criterion_result_id IN (SELECT id FROM criterion_results WHERE evaluation_id=?)"
                           " ORDER BY a.id", (ev["id"],))
    original_in, final_in, items = {}, {}, []
    for c in cl["criteria"]:
        r = results.get(c["id"])
        if not r:
            items.append({"criterion": c, "result": None, "appeals": [], "original": None, "final": None})
            continue
        mine = [a for a in appeals if a["criterion_result_id"] == r["id"]]
        changed = [a for a in mine if a["action"] == "changed"]
        original = {"verdict": r["verdict"], "value": r["value"], "status": r["status"], "source": "original"}
        final = ({"verdict": changed[-1]["new_verdict"], "value": changed[-1]["new_value"], "status": "ok",
                  "source": "decision"} if changed else original)
        original_in[c["id"]], final_in[c["id"]] = original, final
        items.append({"criterion": c, "appeals": mine, "original": original, "final": final,
                      "result": {**r, "evidence_ids": J(r["evidence_ids"], []), "checked_range": J(r["checked_range"]),
                                 "raw_output": None}})
    breakdown = scoring.score(cl, final_in)
    by_id = {b["criterion_id"]: b for b in breakdown["criteria"]}
    for it in items:
        it["score"] = by_id.get(it["criterion"]["id"])
    return {"evaluation": ev, "checklist_version": {k: version[k] for k in ("id", "version", "note", "created_at")},
            "sections": cl.get("sections", []), "items": items, "breakdown": breakdown,
            "original_total": scoring.score(cl, original_in)["total"],
            "cost": sum((r["cost"] or 0) for r in results.values()),
            "models": sorted({r["model"] for r in results.values() if r["model"]})}


@app.get("/api/conversations")
def list_conversations(dataset: str = "", lang: str = "", channel: str = "", status: str = "", q: str = ""):
    sql, args = "SELECT * FROM conversations WHERE 1=1", []
    for col, val in (("dataset_group_id", dataset), ("language", lang.upper()), ("channel", channel)):
        if val:
            sql += f" AND {col}=?"
            args.append(val)
    if q:
        sql += " AND (id LIKE ? OR id IN (SELECT conversation_id FROM utterances WHERE text LIKE ?))"
        args += [f"%{q}%", f"%{q}%"]
    out, vc = [], {}
    with db.connect() as con:
        for c in db.rows(con, sql + " ORDER BY dataset_group_id, id", args):
            ev = pick_evaluation(con, c["id"])
            item = {**c, "warnings": J(c["warnings"], []), "evaluation_id": None, "eval_status": "not_evaluated",
                    "total": None, "provisional": False, "critical_failed": False, "open_appeals": 0,
                    "checklist_version": None}
            if ev:
                v = evaluation_view(con, ev, vc)
                b = v["breakdown"]
                item.update(evaluation_id=ev["id"], eval_status=ev["status"], total=b["total"],
                            provisional=b["provisional"], critical_failed=b["critical_failed"],
                            checklist_version=v["checklist_version"]["version"],
                            open_appeals=sum(1 for i in v["items"] for a in i["appeals"] if a["status"] == "open"))
            match = {"appealed": item["open_appeals"] > 0, "critical": item["critical_failed"]}.get(
                status, item["eval_status"] == status)
            if status and not match:
                continue
            out.append(item)
    return out


@app.get("/api/conversations/{conv_id}")
def get_conversation(conv_id: str, eval: int | None = None):
    with db.connect() as con:
        conv, utts = judge.load_conversation(con, conv_id)
        if not conv:
            not_found("Диалог")
        ev = pick_evaluation(con, conv_id, eval)
        evals = db.rows(con, "SELECT e.id, e.status, e.created_at, v.version FROM evaluations e"
                             " JOIN checklist_versions v ON v.id=e.checklist_version_id WHERE e.conversation_id=?"
                             " ORDER BY v.version DESC", (conv_id,))
        return {"conversation": {**conv, "warnings": J(conv["warnings"], [])}, "utterances": utts,
                "evaluations": evals, "view": evaluation_view(con, ev) if ev else None}


@app.patch("/api/conversations/{conv_id}")
def patch_conversation(conv_id: str, body: dict = Body(...)):
    try:
        off = float(body.get("audio_offset_correction_sec", 0))
    except (TypeError, ValueError):
        raise ValueError("Сдвиг должен быть числом секунд.")
    with db.connect() as con:
        if not con.execute("UPDATE conversations SET audio_offset_correction_sec=? WHERE id=?", (off, conv_id)).rowcount:
            not_found("Диалог")
    return {"ok": True, "audio_offset_correction_sec": off}


@app.get("/api/evaluations/{eval_id}")
def get_evaluation(eval_id: int):
    with db.connect() as con:
        ev = db.row(con, "SELECT * FROM evaluations WHERE id=?", (eval_id,))
        if not ev:
            not_found("Оценка")
        return evaluation_view(con, ev)


# ---------- checklist ----------

@app.get("/api/checklist")
def get_checklist():
    with db.connect() as con:
        active = db.get_state(con, "active_checklist_version_id")
        versions = db.rows(con, "SELECT id, version, parent_version, note, hash, created_at"
                                " FROM checklist_versions ORDER BY id DESC")
    return {"active_version_id": active, "active": cl_mod.get_version(active), "versions": versions}


@app.get("/api/checklist/versions/{vid}")
def get_checklist_version(vid: int):
    return cl_mod.get_version(vid) or not_found("Версия чек-листа")


@app.post("/api/checklist/validate")
def validate_checklist(body: dict = Body(...)):
    return {"errors": cl_mod.validate(body.get("checklist"))}


@app.post("/api/checklist/versions")
def save_checklist(body: dict = Body(...)):
    new = body.get("checklist")
    prev_id = cl_mod.active_version_id()
    prev = cl_mod.get_version(prev_id) if prev_id else None
    vid = cl_mod.save_version(new, note=str(body.get("note") or "")[:300])
    changed = cl_mod.changed_criteria(prev["checklist"], new) if prev else [c["id"] for c in new["criteria"]]
    return {"id": vid, "version": cl_mod.get_version(vid)["version"], "changed_criteria": changed}


@app.post("/api/checklist/activate")
def activate_checklist(body: dict = Body(...)):
    vid = body.get("version_id")
    if not cl_mod.get_version(vid):
        not_found("Версия чек-листа")
    with db.connect() as con:
        db.set_state(con, "active_checklist_version_id", vid)
    return {"ok": True}


@app.post("/api/checklist/preview")
def preview_prompt(body: dict = Body(...)):
    cl = body.get("checklist") or cl_mod.get_version(cl_mod.active_version_id())["checklist"]
    c = next((x for x in cl["criteria"] if x.get("id") == body.get("criterion_id")), None)
    if not c:
        not_found("Критерий")
    with db.connect() as con:
        cid = body.get("conversation_id") or (db.row(con, "SELECT id FROM conversations ORDER BY id LIMIT 1") or {}).get("id")
        conv, utts = judge.load_conversation(con, cid) if cid else (None, [])
    transcript = judge.transcript_block(conv, utts) if conv else "<transcript>…</transcript>\n\n"
    return {"system": judge.SYSTEM, "user": transcript + judge.criterion_block(cl, c, utts), "conversation_id": cid,
            "schema": judge.schema_for(c, [u["uid"] for u in utts] or ["u1"])}


# ---------- runs ----------

@app.post("/api/runs")
async def create_run(body: dict = Body(...)):
    vid = body.get("checklist_version_id") or cl_mod.active_version_id()
    group = body.get("dataset_group_id")
    ids = body.get("conversation_ids")
    if not ids:
        with db.connect() as con:
            ids = [r["id"] for r in db.rows(con, "SELECT id FROM conversations WHERE dataset_group_id=? ORDER BY id",
                                            (group,))]
    return get_run(runs.start_run(ids, vid, group))


@app.post("/api/evaluations")
async def create_evaluation(body: dict = Body(...)):
    cid = body.get("conversation_id")
    with db.connect() as con:
        if not db.row(con, "SELECT id FROM conversations WHERE id=?", (cid,)):
            not_found("Диалог")
    return get_run(runs.start_run([cid], body.get("checklist_version_id") or cl_mod.active_version_id()))


@app.get("/api/runs")
def list_runs():
    with db.connect() as con:
        rs = db.rows(con, "SELECT r.*, v.version FROM runs r LEFT JOIN checklist_versions v"
                          " ON v.id=r.checklist_version_id ORDER BY r.id DESC LIMIT 30")
    for r in rs:
        r["conversations"] = len(J(r.pop("scope"), []))
    return rs


@app.get("/api/runs/{rid}")
def get_run(rid: int):
    with db.connect() as con:
        r = db.row(con, "SELECT r.*, v.version FROM runs r LEFT JOIN checklist_versions v"
                        " ON v.id=r.checklist_version_id WHERE r.id=?", (rid,))
        if not r:
            not_found("Запуск")
        r["conversations"] = len(J(r.pop("scope"), []))
        r["cost"] = db.row(con, "SELECT SUM(cost) s FROM llm_calls WHERE created_at >= ?", (r["started_at"],))["s"] or 0
    return r


# ---------- appeals ----------

@app.post("/api/criterion-results/{rid}/appeals")
def create_appeal(rid: int, body: dict = Body(...)):
    comment = str(body.get("comment") or "").strip()
    if not comment:
        raise ValueError("Добавьте комментарий к апелляции.")
    with db.connect() as con:
        if not db.row(con, "SELECT id FROM criterion_results WHERE id=?", (rid,)):
            not_found("Результат")
        if db.row(con, "SELECT id FROM appeals WHERE criterion_result_id=? AND status='open'", (rid,)):
            raise ValueError("По этому критерию уже есть открытая апелляция.")
        cur = con.execute("INSERT INTO appeals(criterion_result_id,comment,status,created_at) VALUES(?,?,?,?)",
                          (rid, comment[:2000], "open", db.now()))
    return {"id": cur.lastrowid, "status": "open"}


@app.get("/api/appeals")
def list_appeals(status: str = "open"):
    with db.connect() as con:
        rs = db.rows(con, "SELECT a.*, cr.criterion_id, cr.verdict, cr.value, cr.explanation, cr.evidence_ids,"
                          " cr.checked_range, cr.status AS result_status, e.conversation_id, e.checklist_version_id,"
                          " d.action, d.new_verdict, d.new_value, d.reason, d.created_at AS decided_at"
                          " FROM appeals a JOIN criterion_results cr ON cr.id=a.criterion_result_id"
                          " JOIN evaluations e ON e.id=cr.evaluation_id LEFT JOIN decisions d ON d.appeal_id=a.id"
                          + (" WHERE a.status=?" if status else "") + " ORDER BY a.id DESC",
                     (status,) if status else ())
        vc = {}
        for a in rs:
            if a["checklist_version_id"] not in vc:
                vc[a["checklist_version_id"]] = cl_mod.get_version(a["checklist_version_id"])
            a["criterion"] = next((c for c in vc[a["checklist_version_id"]]["checklist"]["criteria"]
                                   if c["id"] == a["criterion_id"]), None)
            a["evidence_ids"] = J(a["evidence_ids"], [])
            a["checked_range"] = J(a["checked_range"])
            utts = db.rows(con, "SELECT uid, idx, speaker, raw_timestamp, text FROM utterances"
                                " WHERE conversation_id=? ORDER BY idx", (a["conversation_id"],))
            anchors = {u["idx"] for u in utts if u["uid"] in a["evidence_ids"]}
            if not anchors and a["checked_range"]:
                anchors = {u["idx"] for u in utts if u["uid"] in a["checked_range"].values()}
            keep = {i for x in anchors for i in range(x - 2, x + 3)}  # cited lines ±2 for context
            a["excerpt"] = [u for u in utts if u["idx"] in keep] or utts[-6:]
    return rs


@app.post("/api/appeals/{aid}/decision")
def decide_appeal(aid: int, body: dict = Body(...)):
    action = body.get("action")
    reason = str(body.get("reason") or "").strip()
    if action not in ("upheld", "changed"):
        raise ValueError("Решение: upheld (подтвердить) или changed (изменить).")
    if not reason:
        raise ValueError("Укажите причину решения.")
    with db.connect() as con:
        a = db.row(con, "SELECT a.*, e.checklist_version_id, cr.criterion_id FROM appeals a"
                        " JOIN criterion_results cr ON cr.id=a.criterion_result_id"
                        " JOIN evaluations e ON e.id=cr.evaluation_id WHERE a.id=?", (aid,))
        if not a:
            not_found("Апелляция")
        if a["status"] != "open":
            raise ValueError("Апелляция уже рассмотрена.")
        nv, val = None, None
        if action == "changed":
            c = next(c for c in cl_mod.get_version(a["checklist_version_id"])["checklist"]["criteria"]
                     if c["id"] == a["criterion_id"])
            nv = body.get("new_verdict")
            if nv not in judge.verdicts(c):
                raise ValueError(f"Новый ответ должен быть одним из: {judge.verdicts(c)}.")
            if nv == "scale":
                try:
                    val = float(body.get("new_value"))
                except (TypeError, ValueError):
                    val = None
                if val not in [float(s["value"]) for s in c["scale"]["steps"]]:
                    raise ValueError("Выберите одну из ступеней шкалы.")
        con.execute("INSERT INTO decisions(appeal_id,action,new_verdict,new_value,reason,created_at) VALUES(?,?,?,?,?,?)",
                    (aid, action, nv, val, reason[:2000], db.now()))
        con.execute("UPDATE appeals SET status='resolved' WHERE id=?", (aid,))
    return {"ok": True}


# ---------- stats / export ----------

@app.get("/api/stats/cost")
def cost_stats():
    with db.connect() as con:
        calls = db.rows(con, "SELECT model, status, latency_ms, cost FROM llm_calls")
        per_eval = [r["s"] or 0 for r in db.rows(con, "SELECT SUM(cost) s FROM criterion_results GROUP BY evaluation_id")]
        tok = db.rows(con, "SELECT SUM(in_tokens) i, SUM(out_tokens) o FROM llm_calls GROUP BY conversation_id")
        nr = db.row(con, "SELECT SUM(status='needs_review') nr, COUNT(*) n FROM criterion_results")
    lat = sorted(c["latency_ms"] for c in calls if c["status"] == "ok")

    def pct(p):
        return lat[min(len(lat) - 1, int(p * len(lat)))] if lat else None
    return {"calls": len(calls), "models": sorted({c["model"] for c in calls}),
            "total_cost": sum(c["cost"] or 0 for c in calls),
            "avg_cost_per_evaluation": statistics.mean(per_eval) if per_eval else None,
            "avg_tokens_per_dialog": ({"in": round(statistics.mean(t["i"] for t in tok)),
                                       "out": round(statistics.mean(t["o"] for t in tok))} if tok else None),
            "latency_ms_p50": pct(0.5), "latency_ms_p95": pct(0.95),
            "invalid_rate": (sum(c["status"] != "ok" for c in calls) / len(calls)) if calls else None,
            "needs_review_rate": ((nr["nr"] or 0) / nr["n"]) if nr and nr["n"] else None}


@app.get("/api/export/{group}.csv")
def export_csv(group: str):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["conversation_id", "language", "channel", "checklist_version", "criterion_id", "criterion", "answer",
                "value", "points", "source", "explanation", "evidence", "total", "critical_failed"])
    with db.connect() as con:
        vc = {}
        for c in db.rows(con, "SELECT * FROM conversations WHERE dataset_group_id=? ORDER BY id", (group,)):
            ev = pick_evaluation(con, c["id"])
            if not ev:
                continue
            v = evaluation_view(con, ev, vc)
            for it in v["items"]:
                f, s, r = it.get("final") or {}, it.get("score") or {}, it.get("result") or {}
                w.writerow([c["source_id"], c["language"], c["channel"], v["checklist_version"]["version"],
                            it["criterion"]["id"], it["criterion"]["title"], f.get("verdict"), f.get("value"),
                            s.get("points"), f.get("source"), r.get("explanation"),
                            " ".join(r.get("evidence_ids") or []), v["breakdown"]["total"],
                            v["breakdown"]["critical_failed"]])
    name = re.sub(r"[^\w.-]", "_", group)
    return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="cqa_{name}.csv"'})


# ---------- audio proxy ----------

@app.get("/api/audio/{conv_id}")
async def audio(conv_id: str):
    # only the URL stored on the conversation is fetched (no SSRF via arbitrary URLs)
    with db.connect() as con:
        conv = db.row(con, "SELECT audio_url FROM conversations WHERE id=?", (conv_id,))
    if not conv or not conv["audio_url"]:
        not_found("Запись звонка")
    d = config.DATA_DIR / "audio"
    d.mkdir(parents=True, exist_ok=True)
    path = d / (re.sub(r"[^\w.-]", "_", conv_id) + ".mp3")
    if not path.exists():
        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as cli:
            r = await cli.get(conv["audio_url"])
            if "text/html" in r.headers.get("content-type", ""):
                # organizers' link is a player page; the real mp3 is a short-lived signed <source src>
                m = re.search(r'<source[^>]+src="([^"]+)"', r.text)
                src = html.unescape(m.group(1)) if m else ""
                if not src.startswith("https://"):
                    raise HTTPException(502, "На странице записи не найден аудиофайл.")
                r = await cli.get(src)
            if r.status_code >= 400 or not r.content:
                raise HTTPException(502, f"Не удалось скачать запись ({r.status_code}).")
            path.write_bytes(r.content)
    return FileResponse(path, media_type="audio/mpeg")  # FileResponse handles Range, so seeking works


app.mount("/", StaticFiles(directory=config.ROOT / "frontend", html=True), name="frontend")
