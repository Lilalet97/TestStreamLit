# core/db.py
import sqlite3
from datetime import datetime
import streamlit as st
from typing import Optional

from core.redact import json_dumps_safe
from core.config import AppConfig


def _db(cfg: AppConfig):
    conn = sqlite3.connect(cfg.runs_db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
    except Exception:
        pass
    return conn


def now_iso():
    return datetime.utcnow().isoformat() + "Z"


def init_db(cfg: AppConfig):
    conn = _db(cfg)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS runs (
            run_id TEXT PRIMARY KEY,
            created_at TEXT,
            user_id TEXT,
            session_id TEXT,
            provider TEXT,
            operation TEXT,
            endpoint TEXT,
            request_json TEXT,
            http_status INTEGER,
            response_text TEXT,
            response_json TEXT,
            state TEXT,
            job_id TEXT,
            output_json TEXT,
            gpt_analysis TEXT,
            error_text TEXT,
            duration_ms INTEGER
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS active_jobs (
            run_id TEXT PRIMARY KEY,
            created_at TEXT,
            updated_at TEXT,
            user_id TEXT,
            session_id TEXT,
            provider TEXT,
            operation TEXT,
            state TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS api_keys (
          api_key_id INTEGER PRIMARY KEY AUTOINCREMENT,
          provider TEXT NOT NULL,
          key_name TEXT NOT NULL,
          key_payload TEXT NOT NULL,
          concurrency_limit INTEGER NOT NULL DEFAULT 1,
          rpm_limit INTEGER,
          priority INTEGER NOT NULL DEFAULT 0,
          tenant_scope TEXT,
          is_active INTEGER NOT NULL DEFAULT 1,
          expires_at TEXT,
          created_at TEXT,
          updated_at TEXT,
          UNIQUE(provider, key_name)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS api_key_leases (
          lease_id TEXT PRIMARY KEY,
          api_key_id INTEGER NOT NULL,
          provider TEXT NOT NULL,
          run_id TEXT NOT NULL,
          user_id TEXT,
          session_id TEXT,
          school_id TEXT,
          state TEXT NOT NULL,
          acquired_at TEXT NOT NULL,
          last_heartbeat_at TEXT NOT NULL,
          released_at TEXT,
          ttl_sec INTEGER NOT NULL DEFAULT 120,
          FOREIGN KEY(api_key_id) REFERENCES api_keys(api_key_id)
        )
    """)

    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_api_key_leases_provider_state
        ON api_key_leases(provider, state, last_heartbeat_at)
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS api_key_usage_minute (
          api_key_id INTEGER NOT NULL,
          minute_bucket TEXT NOT NULL,
          count INTEGER NOT NULL,
          PRIMARY KEY(api_key_id, minute_bucket),
          FOREIGN KEY(api_key_id) REFERENCES api_keys(api_key_id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS api_key_waiters (
          waiter_id TEXT PRIMARY KEY,
          provider TEXT NOT NULL,
          run_id TEXT NOT NULL,
          user_id TEXT,
          session_id TEXT,
          school_id TEXT,
          enqueued_at TEXT NOT NULL,
          state TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          UNIQUE(provider, run_id)
        )
    """)

    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_api_key_waiters_provider_state
        ON api_key_waiters(provider, state, enqueued_at)
    """)
    conn.commit()
    conn.close()


def insert_run(cfg: AppConfig, row: dict):
    conn = _db(cfg)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO runs (
            run_id, created_at, user_id, session_id, provider, operation, endpoint,
            request_json, http_status, response_text, response_json, state, job_id,
            output_json, gpt_analysis, error_text, duration_ms
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        row["run_id"], row["created_at"], row["user_id"], row["session_id"], row["provider"], row["operation"], row["endpoint"],
        row.get("request_json"), row.get("http_status"), row.get("response_text"), row.get("response_json"),
        row.get("state"), row.get("job_id"), row.get("output_json"), row.get("gpt_analysis"), row.get("error_text"),
        row.get("duration_ms"),
    ))
    conn.commit()
    conn.close()


def update_run(cfg: AppConfig, run_id: str, **fields):
    if not fields:
        return
    conn = _db(cfg)
    cur = conn.cursor()
    cols = ", ".join([f"{k}=?" for k in fields.keys()])
    vals = list(fields.values())
    vals.append(run_id)
    cur.execute(f"UPDATE runs SET {cols} WHERE run_id=?", vals)
    conn.commit()
    conn.close()


def add_active_job(cfg: AppConfig, run_id: str, provider: str, operation: str, state: str):
    conn = _db(cfg)
    cur = conn.cursor()
    cur.execute("""
        INSERT OR REPLACE INTO active_jobs (
            run_id, created_at, updated_at, user_id, session_id, provider, operation, state
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        run_id, now_iso(), now_iso(), st.session_state.user_id, st.session_state.session_id,
        provider, operation, state
    ))
    conn.commit()
    conn.close()


def touch_active_job(cfg: AppConfig, run_id: str, state: Optional[str] = None):
    conn = _db(cfg)
    cur = conn.cursor()
    if state is None:
        cur.execute("UPDATE active_jobs SET updated_at=? WHERE run_id=?", (now_iso(), run_id))
    else:
        cur.execute("UPDATE active_jobs SET updated_at=?, state=? WHERE run_id=?", (now_iso(), state, run_id))
    conn.commit()
    conn.close()


def remove_active_job(cfg: AppConfig, run_id: str):
    conn = _db(cfg)
    cur = conn.cursor()
    cur.execute("DELETE FROM active_jobs WHERE run_id=?", (run_id,))
    conn.commit()
    conn.close()


def count_active_jobs(cfg: AppConfig, user_id: Optional[str] = None) -> int:
    conn = _db(cfg)
    cur = conn.cursor()
    if user_id:
        cur.execute("SELECT COUNT(*) AS c FROM active_jobs WHERE user_id=? AND state IN ('running','submitted')", (user_id,))
    else:
        cur.execute("SELECT COUNT(*) AS c FROM active_jobs WHERE state IN ('running','submitted')")
    c = int(cur.fetchone()["c"])
    conn.close()
    return c


def guard_concurrency_or_raise(cfg: AppConfig):
    if count_active_jobs(cfg, st.session_state.user_id) >= cfg.user_max_concurrency:
        raise RuntimeError(f"사용자 동시 실행 제한({cfg.user_max_concurrency})을 초과했습니다.")
    if count_active_jobs(cfg, None) >= cfg.global_max_concurrency:
        raise RuntimeError(f"전체 동시 실행 제한({cfg.global_max_concurrency})을 초과했습니다.")


def list_runs(cfg: AppConfig, user_id: str, session_only: bool, limit: int = 30):
    conn = _db(cfg)
    cur = conn.cursor()
    if session_only:
        cur.execute("""
            SELECT * FROM runs
            WHERE user_id=? AND session_id=?
            ORDER BY created_at DESC
            LIMIT ?
        """, (user_id, st.session_state.session_id, limit))
    else:
        cur.execute("""
            SELECT * FROM runs
            WHERE user_id=?
            ORDER BY created_at DESC
            LIMIT ?
        """, (user_id, limit))
    rows = cur.fetchall()
    conn.close()
    return rows


def get_run(cfg: AppConfig, run_id: str):
    conn = _db(cfg)
    cur = conn.cursor()
    cur.execute("SELECT * FROM runs WHERE run_id=?", (run_id,))
    row = cur.fetchone()
    conn.close()
    return row


def cleanup_orphan_active_jobs(cfg: AppConfig):
    """
    TTL 기준으로 오래 업데이트되지 않은 running/submitted job만 정리합니다.
    - updated_at은 UTC ISO8601 + 'Z' 포맷 가정 (문자열 비교 안전)
    """
    conn = None
    try:
        cutoff_ts = datetime.utcnow().timestamp() - cfg.active_job_ttl_sec
        cutoff_str = datetime.utcfromtimestamp(cutoff_ts).isoformat() + "Z"

        conn = _db(cfg)
        cur = conn.cursor()
        cur.execute("""
            DELETE FROM active_jobs
            WHERE state IN ('running','submitted')
              AND (updated_at IS NULL OR updated_at = '' OR updated_at < ?)
        """, (cutoff_str,))
        conn.commit()
    except Exception:
        # 조용히 실패(운영 안정성)
        pass
    finally:
        try:
            if conn:
                conn.close()
        except Exception:
            pass


def clear_my_active_jobs(cfg: AppConfig, session_only: bool = False, only_stale: bool = False):
    """
    내 active_jobs 중 running/submitted만 삭제.
    - session_only=True: 현재 세션만
    - only_stale=True: TTL 기준 지난 것만
    """
    conn = _db(cfg)
    cur = conn.cursor()

    params = []
    where = ["user_id = ?", "state IN ('running','submitted')"]
    params.append(st.session_state.user_id)

    if session_only:
        where.append("session_id = ?")
        params.append(st.session_state.session_id)

    if only_stale:
        cutoff_ts = datetime.utcnow().timestamp() - cfg.active_job_ttl_sec
        cutoff_str = datetime.utcfromtimestamp(cutoff_ts).isoformat() + "Z"
        where.append("(updated_at IS NULL OR updated_at = '' OR updated_at < ?)")
        params.append(cutoff_str)

    sql = f"DELETE FROM active_jobs WHERE {' AND '.join(where)}"
    cur.execute(sql, tuple(params))

    conn.commit()
    conn.close()
