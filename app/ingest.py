import json
import re
from datetime import datetime, timezone
from urllib.parse import quote

import httpx

from app import config, db
from app.checklist import sha

SPEAKERS = {"client", "operator", "bot", "system"}
_FRAC = re.compile(r"(\.\d{6})\d+")


def parse_offset(ts):
    """MM:SS / H:MM:SS -> seconds; ISO 8601 -> datetime (caller subtracts the first one)."""
    ts = (ts or "").strip()
    if re.fullmatch(r"\d+:\d{1,2}(:\d{1,2})?", ts):
        sec = 0
        for p in ts.split(":"):
            sec = sec * 60 + int(p)
        return float(sec)
    # calls come as 2026-11-03T13:10:00.6388245+00:00: 7 fraction digits, trim to 6 for fromisoformat
    return datetime.fromisoformat(_FRAC.sub(r"\1", ts.replace("Z", "+00:00")))


def unwrap(payload):
    """full API response, {records:[...]}, a list, or a single record -> (group_id|None, records)"""
    if isinstance(payload, list):
        return None, payload
    if not isinstance(payload, dict):
        raise ValueError("Ожидался JSON-объект или массив.")
    if "isSuccess" in payload:
        if not payload.get("isSuccess"):
            raise ValueError(f"API вернул ошибку: {payload.get('error') or 'isSuccess=false'}")
        payload = payload.get("data") or {}
    if "records" in payload:
        return payload.get("dataset_group_id"), payload["records"] or []
    if "transcript" in payload:
        return None, [payload]
    raise ValueError("Не найдено поле records или transcript.")


def normalize(rec):
    if not isinstance(rec, dict):
        raise ValueError("запись не является объектом")
    cid = str(rec.get("conversation_id") or "").strip()
    if not cid:
        raise ValueError("нет conversation_id")
    tr = rec.get("transcript")
    if isinstance(tr, str):
        tr = json.loads(tr)
    if not isinstance(tr, list) or not tr:
        raise ValueError("пустой transcript")
    warnings, utts, first = [], [], None
    for i, t in enumerate(tr, 1):
        t = t if isinstance(t, dict) else {}
        sp = str(t.get("speaker", "")).strip().lower()
        if sp not in SPEAKERS:
            warnings.append(f"u{i}: неизвестная роль «{t.get('speaker')}»")
        raw = str(t.get("timestamp", ""))
        try:
            off = parse_offset(raw)
            if isinstance(off, datetime):
                first = first or off
                off = (off - first).total_seconds()
        except ValueError:
            off = None
            warnings.append(f"u{i}: не удалось разобрать время «{raw}»")
        utts.append({"uid": f"u{i}", "idx": i, "speaker": sp, "raw_timestamp": raw,
                     "offset_sec": off, "text": str(t.get("text", ""))})
    conv = {"source_id": cid, "channel": rec.get("channel") or "", "language": (rec.get("language") or "").upper(),
            "audio_url": rec.get("audio_url") or None, "conversation_hash": sha(tr)}
    return conv, utts, warnings


def import_records(group_id, records, raw_path=None):
    report = {"group_id": group_id, "imported": 0, "updated": 0, "unchanged": 0, "errors": [], "warnings": []}
    with db.connect() as con:
        for n, rec in enumerate(records, 1):
            try:
                conv, utts, warns = normalize(rec)
            except Exception as e:  # one bad record never kills the batch
                rid = rec.get("conversation_id") if isinstance(rec, dict) else None
                report["errors"].append({"record": n, "id": rid, "error": str(e)})
                continue
            cid = conv["source_id"]
            old = db.row(con, "SELECT * FROM conversations WHERE id=?", (cid,))
            if old and old["dataset_group_id"] != group_id and old["conversation_hash"] != conv["conversation_hash"]:
                cid = f"{conv['source_id']}@{group_id}"  # same id reused by another set
                old = db.row(con, "SELECT * FROM conversations WHERE id=?", (cid,))
            if old and old["conversation_hash"] == conv["conversation_hash"]:
                con.execute("UPDATE conversations SET dataset_group_id=? WHERE id=?", (group_id, cid))
                report["unchanged"] += 1
                continue
            rev = (old["revision"] + 1) if old else 1
            if old:
                report["updated"] += 1
                report["warnings"].append(f"{cid}: транскрипт изменился, сохранена ревизия {rev}")
            else:
                report["imported"] += 1
            report["warnings"] += [f"{cid}: {w}" for w in warns]
            con.execute(
                "INSERT INTO conversations(id,source_id,dataset_group_id,channel,language,audio_url,conversation_hash,revision,warnings)"
                " VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET dataset_group_id=excluded.dataset_group_id,"
                " channel=excluded.channel, language=excluded.language, audio_url=excluded.audio_url,"
                " conversation_hash=excluded.conversation_hash, revision=excluded.revision, warnings=excluded.warnings",
                (cid, conv["source_id"], group_id, conv["channel"], conv["language"], conv["audio_url"],
                 conv["conversation_hash"], rev, json.dumps(warns, ensure_ascii=False)))
            con.execute("DELETE FROM utterances WHERE conversation_id=?", (cid,))
            con.executemany(
                "INSERT INTO utterances(conversation_id,uid,idx,speaker,raw_timestamp,offset_sec,text) VALUES(?,?,?,?,?,?,?)",
                [(cid, u["uid"], u["idx"], u["speaker"], u["raw_timestamp"], u["offset_sec"], u["text"]) for u in utts])
        count = db.row(con, "SELECT COUNT(*) n FROM conversations WHERE dataset_group_id=?", (group_id,))["n"]
        con.execute("INSERT INTO datasets(group_id,imported_at,record_count,raw_path) VALUES(?,?,?,?)"
                    " ON CONFLICT(group_id) DO UPDATE SET imported_at=excluded.imported_at,"
                    " record_count=excluded.record_count, raw_path=excluded.raw_path",
                    (group_id, db.now(), count, str(raw_path) if raw_path else None))
    report["total_in_set"] = count
    return report


def save_raw(group_id, payload_bytes):
    d = config.DATA_DIR / "raw"
    d.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w.-]", "_", group_id)
    p = d / f"{safe}_{datetime.now(timezone.utc):%Y%m%dT%H%M%S}.json"
    p.write_bytes(payload_bytes)
    return p


def fetch_group(group_id):
    group_id = (group_id or "").strip()
    if not group_id:
        raise ValueError("Введите group_id.")
    if "{group_id}" not in config.API_URL_TEMPLATE:
        raise ValueError("CQA_API_URL_TEMPLATE не задан в .env (нужен адрес с {group_id}).")
    url = config.API_URL_TEMPLATE.replace("{group_id}", quote(group_id, safe=""))
    resp = httpx.get(url, timeout=120)
    if resp.status_code >= 400:
        raise ValueError(f"API организаторов ответил {resp.status_code}.")
    raw = save_raw(group_id, resp.content)
    gid, records = unwrap(resp.json())
    return import_records(gid or group_id, records, raw)


def import_payload(payload_bytes, group_id=None):
    gid, records = unwrap(json.loads(payload_bytes))
    gid = (group_id or gid or "UPLOAD").strip()
    raw = save_raw(gid, payload_bytes)
    return import_records(gid, records, raw)
