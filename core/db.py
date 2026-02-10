# core/db.py
import sqlite3
from datetime import datetime
import streamlit as st
from typing import Optional
import uuid
from datetime import timedelta
import os

try:
    import libsql_experimental as libsql
    _HAS_LIBSQL = True
except ImportError:
    _HAS_LIBSQL = False

from core.redact import json_dumps_safe
from core.config import AppConfig

# ── libsql 호환 dict-row wrapper ──────────────────────────
class _DictCursor:
    """DB-API cursor를 감싸서 fetchone/fetchall이 dict를 반환."""
    __slots__ = ("_cur",)
    def __init__(self, cur): self._cur = cur
    def execute(self, *a, **kw):
        self._cur.execute(*a, **kw); return self
    def fetchone(self):
        row = self._cur.fetchone()
        if row is None or not self._cur.description: return row
        return dict(zip([d[0] for d in self._cur.description], row))
    def fetchall(self):
        rows = self._cur.fetchall()
        if not rows or not self._cur.description: return rows
        cols = [d[0] for d in self._cur.description]
        return [dict(zip(cols, r)) for r in rows]
    @property
    def description(self): return self._cur.description
    @property
    def lastrowid(self): return self._cur.lastrowid

class _DictConn:
    """libsql connection을 감싸서 cursor가 dict row를 반환하도록 함."""
    __slots__ = ("_conn",)
    def __init__(self, conn): self._conn = conn
    def cursor(self): return _DictCursor(self._conn.cursor())
    def execute(self, *a, **kw): return _DictCursor(self._conn.execute(*a, **kw))
    def commit(self): self._conn.commit()
    def close(self): pass   # cached → 닫지 않음
# ──────────────────────────────────────────────────────────

_cached_conn = None      # libsql 커넥션 캐시 (생성 비용이 크므로 1회만)

def _db(cfg: AppConfig):
    global _cached_conn
    url = cfg.turso_database_url
    token = cfg.turso_auth_token

    # libsql 경로: 캐시된 커넥션 재사용
    if _HAS_LIBSQL and _cached_conn is not None:
        return _DictConn(_cached_conn)

    if url and _HAS_LIBSQL:
        raw = libsql.connect(cfg.runs_db_path, sync_url=url, auth_token=token)
        try:
            raw.sync()
        except Exception:
            pass
        _cached_conn = raw
        conn = _DictConn(raw)
    elif _HAS_LIBSQL:
        raw = libsql.connect(cfg.runs_db_path)
        _cached_conn = raw
        conn = _DictConn(raw)
    else:
        # libsql 미설치 시 (Windows 개발환경 등) 기존 sqlite3 fallback
        conn = sqlite3.connect(cfg.runs_db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
    except Exception:
        pass
    return conn


def now_iso():
    return datetime.utcnow().isoformat() + "Z"


_DB_INITIALIZED = False

def init_db(cfg: AppConfig):
    global _DB_INITIALIZED
    if _DB_INITIALIZED:
        return
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
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user',
            school_id TEXT NOT NULL DEFAULT 'default',
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT,
            updated_at TEXT
        )
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_users_role_active
        ON users(role, is_active)
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_sessions (
            session_token TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            role TEXT NOT NULL,
            school_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            revoked INTEGER NOT NULL DEFAULT 0
        )
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_user_sessions_user
        ON user_sessions(user_id)
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_user_sessions_expires
        ON user_sessions(expires_at)
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
    _DB_INITIALIZED = True


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


# ----------------------------
# Auth / Users
# ----------------------------

def users_exist(cfg: AppConfig) -> bool:
    conn = _db(cfg)
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS c FROM users")
        return int(cur.fetchone()["c"]) > 0
    finally:
        conn.close()


def get_user(cfg: AppConfig, user_id: str):
    conn = _db(cfg)
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
        return cur.fetchone()
    finally:
        conn.close()


def list_users(cfg: AppConfig, include_inactive: bool = True):
    conn = _db(cfg)
    try:
        cur = conn.cursor()
        if include_inactive:
            cur.execute("SELECT * FROM users ORDER BY role DESC, user_id ASC")
        else:
            cur.execute("SELECT * FROM users WHERE is_active=1 ORDER BY role DESC, user_id ASC")
        return cur.fetchall()
    finally:
        conn.close()


def upsert_user(cfg: AppConfig, user_id: str, password_hash: str, role: str = 'user', school_id: str = 'default', is_active: int = 1):
    conn = _db(cfg)
    try:
        cur = conn.cursor()
        ts = now_iso()
        cur.execute(
            """
            INSERT INTO users (user_id, password_hash, role, school_id, is_active, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                password_hash=excluded.password_hash,
                role=excluded.role,
                school_id=excluded.school_id,
                is_active=excluded.is_active,
                updated_at=excluded.updated_at
            """,
            (user_id, password_hash, role, school_id, int(is_active), ts, ts),
        )
        conn.commit()
    finally:
        conn.close()


def update_user_fields(cfg: AppConfig, user_id: str, *, role: str | None = None, school_id: str | None = None):
    """role, school_id 중 변경할 필드만 업데이트."""
    parts, params = [], []
    if role is not None:
        parts.append("role=?"); params.append(role)
    if school_id is not None:
        parts.append("school_id=?"); params.append(school_id)
    if not parts:
        return
    parts.append("updated_at=?"); params.append(now_iso())
    params.append(user_id)
    conn = _db(cfg)
    try:
        conn.execute(f"UPDATE users SET {', '.join(parts)} WHERE user_id=?", params)
        conn.commit()
    finally:
        conn.close()


def set_user_password(cfg: AppConfig, user_id: str, password_hash: str):
    conn = _db(cfg)
    try:
        cur = conn.cursor()
        cur.execute("UPDATE users SET password_hash=?, updated_at=? WHERE user_id=?", (password_hash, now_iso(), user_id))
        conn.commit()
    finally:
        conn.close()


def set_user_active(cfg: AppConfig, user_id: str, is_active: bool):
    conn = _db(cfg)
    try:
        cur = conn.cursor()
        cur.execute("UPDATE users SET is_active=?, updated_at=? WHERE user_id=?", (1 if is_active else 0, now_iso(), user_id))
        conn.commit()
    finally:
        conn.close()


def hard_delete_user(cfg: AppConfig, user_id: str):
    conn = _db(cfg)
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM users WHERE user_id=?", (user_id,))
        conn.commit()
    finally:
        conn.close()

def _expires_iso(ttl_sec: int) -> str:
    dt = datetime.utcnow() + timedelta(seconds=int(ttl_sec))
    return dt.isoformat() + "Z"


def create_user_session(cfg: AppConfig, user_id: str, role: str, school_id: str, ttl_sec: int = 86400) -> str:
    """Create a persistent login session and return opaque token."""
    token = uuid.uuid4().hex
    conn = _db(cfg)
    try:
        cur = conn.cursor()
        now = now_iso()
        exp = _expires_iso(ttl_sec)
        cur.execute(
            "INSERT INTO user_sessions(session_token,user_id,role,school_id,created_at,expires_at,last_seen,revoked) "
            "VALUES (?,?,?,?,?,?,?,0)",
            (token, user_id, role, school_id, now, exp, now),
        )
        conn.commit()
        if getattr(cfg, "debug_auth", False):
            import streamlit as st
            st.sidebar.success(f"[AUTH-DBG] session created token head={token[:6]} exp={exp}")
        return token
    finally:
        conn.close()


def get_user_session(cfg: AppConfig, token: str):
    if not token:
        return None
    conn = _db(cfg)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM user_sessions WHERE session_token=? AND revoked=0 AND expires_at>?",
            (token, now_iso()),
        )
        return cur.fetchone()
    finally:
        conn.close()


def touch_user_session(cfg: AppConfig, token: str):
    if not token:
        return
    conn = _db(cfg)
    try:
        cur = conn.cursor()
        cur.execute("UPDATE user_sessions SET last_seen=? WHERE session_token=?", (now_iso(), token))
        conn.commit()
    finally:
        conn.close()


def revoke_user_session(cfg: AppConfig, token: str):
    if not token:
        return
    conn = _db(cfg)
    try:
        cur = conn.cursor()
        cur.execute("UPDATE user_sessions SET revoked=1, last_seen=? WHERE session_token=?", (now_iso(), token))
        conn.commit()
    finally:
        conn.close()


# ----------------------------
# Admin helpers (read-only)
# ----------------------------

def list_active_jobs_all(cfg: AppConfig, limit: int = 200, user_id: str | None = None):
    conn = _db(cfg)
    try:
        cur = conn.cursor()
        if user_id:
            cur.execute(
                """SELECT * FROM active_jobs WHERE user_id=? ORDER BY updated_at DESC LIMIT ?""",
                (user_id, limit),
            )
        else:
            cur.execute(
                """SELECT * FROM active_jobs ORDER BY updated_at DESC LIMIT ?""",
                (limit,),
            )
        return cur.fetchall()
    finally:
        conn.close()


def list_runs_admin(cfg: AppConfig, limit: int = 200, user_id: str | None = None):
    conn = _db(cfg)
    try:
        cur = conn.cursor()
        if user_id:
            cur.execute(
                """SELECT * FROM runs WHERE user_id=? ORDER BY created_at DESC LIMIT ?""",
                (user_id, limit),
            )
        else:
            cur.execute(
                """SELECT * FROM runs ORDER BY created_at DESC LIMIT ?""",
                (limit,),
            )
        return cur.fetchall()
    finally:
        conn.close()


def list_key_waiters(cfg: AppConfig, limit: int = 200):
    conn = _db(cfg)
    try:
        cur = conn.cursor()
        cur.execute(
            """SELECT * FROM api_key_waiters ORDER BY enqueued_at ASC LIMIT ?""",
            (limit,),
        )
        return cur.fetchall()
    finally:
        conn.close()


def list_key_leases(cfg: AppConfig, limit: int = 200):
    conn = _db(cfg)
    try:
        cur = conn.cursor()
        cur.execute(
            """SELECT * FROM api_key_leases ORDER BY acquired_at DESC LIMIT ?""",
            (limit,),
        )
        return cur.fetchall()
    finally:
        conn.close()
