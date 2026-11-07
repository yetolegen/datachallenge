"""Background batch runs. Progress lives in the runs table, so the UI just polls GET /api/runs/{id}.
Resume = start again: everything already judged comes back from the cache."""
import asyncio
import json
import logging

from app import db
from app.checklist import get_version
from app.judge import evaluate_conversation

log = logging.getLogger("cqa.runs")
_tasks = set()  # keep references so tasks aren't garbage-collected mid-run


def start_run(conv_ids, version_id, group=None):
    v = get_version(version_id)
    if not v:
        raise ValueError("Версия чек-листа не найдена.")
    if not conv_ids:
        raise ValueError("Нет диалогов для оценки. Сначала загрузите набор.")
    with db.connect() as con:
        cur = con.execute(
            "INSERT INTO runs(dataset_group_id,checklist_version_id,status,total,scope,started_at) VALUES(?,?,?,?,?,?)",
            (group, version_id, "running", len(conv_ids) * len(v["checklist"]["criteria"]), json.dumps(conv_ids), db.now()))
        rid = cur.lastrowid
    t = asyncio.get_running_loop().create_task(_run(rid, conv_ids, v))
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)
    return rid


async def _run(rid, conv_ids, version):
    def progress(hit, status):
        with db.connect() as con:
            con.execute("UPDATE runs SET done=done+1, cache_hits=cache_hits+?, new_calls=new_calls+?,"
                        " needs_review=needs_review+? WHERE id=?",
                        (int(hit), int(not hit), int(status == "needs_review"), rid))

    errors = []

    async def one(cid):
        try:
            await evaluate_conversation(cid, version, rid, progress)
        except Exception as e:  # noqa: BLE001 - per-dialog failure, the batch goes on
            log.exception("run %s: %s failed", rid, cid)
            errors.append(f"{cid}: {e}")

    await asyncio.gather(*(one(c) for c in conv_ids))
    with db.connect() as con:
        con.execute("UPDATE runs SET status=?, error=?, finished_at=? WHERE id=?",
                    ("done" if not errors else "done_with_errors", "\n".join(errors)[:4000] or None, db.now(), rid))


def mark_interrupted():
    with db.connect() as con:
        con.execute("UPDATE runs SET status='interrupted', finished_at=? WHERE status='running'", (db.now(),))
        con.execute("UPDATE evaluations SET status='interrupted' WHERE status='running'")
