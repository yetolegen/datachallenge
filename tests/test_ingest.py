import json

import pytest

from app import db, ingest


def rec(cid, lang="RU", channel="agent chat", ts=("00:02", "00:10")):
    return {"conversation_id": cid, "channel": channel, "language": lang, "audio_url": None,
            "transcript": [{"timestamp": ts[0], "speaker": "client", "text": "Привет"},
                           {"timestamp": ts[1], "speaker": "operator", "text": "Здравствуйте! Меня зовут Алина."}]}


def test_offsets():
    assert ingest.parse_offset("01:15") == 75
    assert ingest.parse_offset("1:02:03") == 3723
    _, utts, warns = ingest.normalize(rec("x", channel="voice call",
                                          ts=("2026-11-03T13:10:00.6388245+00:00", "2026-11-03T13:10:15.5558925+00:00")))
    assert warns == [] and utts[0]["offset_sec"] == 0 and round(utts[1]["offset_sec"], 2) == 14.92
    assert utts[1]["raw_timestamp"] == "2026-11-03T13:10:15.5558925+00:00"  # original kept


def test_unwrap_shapes():
    full = {"error": "", "isSuccess": True, "data": {"dataset_group_id": "G", "records": [rec("a")]}}
    assert ingest.unwrap(full)[0] == "G"
    assert len(ingest.unwrap([rec("a"), rec("b")])[1]) == 2
    assert len(ingest.unwrap(rec("a"))[1]) == 1
    with pytest.raises(ValueError):
        ingest.unwrap({"isSuccess": False, "error": "boom"})


def test_import_is_idempotent_and_reports_bad_records():
    db.init()
    rep = ingest.import_records("T1", [rec("t-1"), rec("t-2"), {"conversation_id": "bad", "transcript": []}])
    assert rep["imported"] == 2 and len(rep["errors"]) == 1
    assert ingest.import_records("T1", [rec("t-1")])["unchanged"] == 1
    changed = rec("t-1")
    changed["transcript"][1]["text"] = "Другой текст"
    assert ingest.import_records("T1", [changed])["updated"] == 1
    with db.connect() as con:
        assert db.row(con, "SELECT revision FROM conversations WHERE id='t-1'")["revision"] == 2


def test_upload_payload():
    db.init()
    payload = json.dumps({"isSuccess": True, "error": "", "data": {"dataset_group_id": "UP", "records": [rec("u-1", "KZ")]}})
    rep = ingest.import_payload(payload.encode())
    assert rep["group_id"] == "UP" and rep["imported"] == 1
