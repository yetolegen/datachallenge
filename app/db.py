import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from app import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS datasets (
  group_id TEXT PRIMARY KEY, imported_at TEXT, record_count INTEGER, raw_path TEXT);
CREATE TABLE IF NOT EXISTS conversations (
  id TEXT PRIMARY KEY, source_id TEXT, dataset_group_id TEXT, channel TEXT, language TEXT,
  audio_url TEXT, conversation_hash TEXT, revision INTEGER DEFAULT 1,
  audio_offset_correction_sec REAL DEFAULT 0, warnings TEXT DEFAULT '[]');
CREATE TABLE IF NOT EXISTS utterances (
  conversation_id TEXT, uid TEXT, idx INTEGER, speaker TEXT, raw_timestamp TEXT,
  offset_sec REAL, text TEXT, PRIMARY KEY (conversation_id, uid));
CREATE TABLE IF NOT EXISTS checklist_versions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, version INTEGER, parent_version INTEGER,
  json TEXT, hash TEXT, note TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS app_state (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, dataset_group_id TEXT, checklist_version_id INTEGER,
  status TEXT, total INTEGER DEFAULT 0, done INTEGER DEFAULT 0, cache_hits INTEGER DEFAULT 0,
  new_calls INTEGER DEFAULT 0, needs_review INTEGER DEFAULT 0, error TEXT, scope TEXT,
  started_at TEXT, finished_at TEXT);
CREATE TABLE IF NOT EXISTS evaluations (
  id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT, checklist_version_id INTEGER,
  run_id INTEGER, status TEXT, created_at TEXT, UNIQUE (conversation_id, checklist_version_id));
CREATE TABLE IF NOT EXISTS criterion_results (
  id INTEGER PRIMARY KEY AUTOINCREMENT, evaluation_id INTEGER, criterion_id TEXT,
  criterion_hash TEXT, verdict TEXT, value REAL, evidence_ids TEXT, checked_range TEXT,
  na_reason TEXT, critical_violation INTEGER, explanation TEXT, reasoning TEXT, confidence TEXT,
  status TEXT, cache_key TEXT, raw_output TEXT, model TEXT, prompt_version TEXT, cost REAL,
  UNIQUE (evaluation_id, criterion_id));
CREATE INDEX IF NOT EXISTS ix_cr_cache ON criterion_results (cache_key);
CREATE TABLE IF NOT EXISTS judge_cache (cache_key TEXT PRIMARY KEY, result_json TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS appeals (
  id INTEGER PRIMARY KEY AUTOINCREMENT, criterion_result_id INTEGER, comment TEXT,
  status TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS decisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, appeal_id INTEGER UNIQUE, action TEXT,
  new_verdict TEXT, new_value REAL, reason TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS llm_calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT, cache_key TEXT, conversation_id TEXT, criterion_id TEXT,
  provider TEXT, model TEXT, prompt_version TEXT, in_tokens INTEGER, out_tokens INTEGER,
  latency_ms INTEGER, cost REAL, status TEXT, error TEXT, created_at TEXT);
"""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect():
    # one short-lived connection per unit of work; WAL lets the batch runner and API overlap
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(config.DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=30000")
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init():
    with connect() as con:
        con.executescript(SCHEMA)


def rows(con, sql, args=()):
    return [dict(r) for r in con.execute(sql, args).fetchall()]


def row(con, sql, args=()):
    r = con.execute(sql, args).fetchone()
    return dict(r) if r else None


def get_state(con, key, default=None):
    r = con.execute("SELECT value FROM app_state WHERE key=?", (key,)).fetchone()
    return json.loads(r[0]) if r else default


def set_state(con, key, value):
    con.execute("INSERT INTO app_state(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value, ensure_ascii=False)))
