# core/db.py
from datetime import datetime, timedelta
import streamlit as st
from typing import Optional
import uuid

import json

from core.redact import json_dumps_safe
from core.config import AppConfig
from core.database import get_db, force_sync


def now_iso():
    return datetime.utcnow().isoformat() + "Z"


_DB_INITIALIZED = False

def init_db(cfg: AppConfig):
    global _DB_INITIALIZED
    if _DB_INITIALIZED:
        return
    conn = get_db(cfg)
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

    cur.execute("""
        CREATE TABLE IF NOT EXISTS mj_gallery (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id TEXT NOT NULL,
          created_at TEXT NOT NULL,
          display_date TEXT NOT NULL,
          prompt TEXT NOT NULL,
          tags_json TEXT,
          aspect_ratio TEXT DEFAULT '1:1',
          settings_json TEXT,
          images_json TEXT,
          attached_images_json TEXT
        )
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_mj_gallery_user
        ON mj_gallery(user_id, created_at DESC)
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS gpt_conversations (
          id          TEXT PRIMARY KEY,
          user_id     TEXT NOT NULL,
          title       TEXT NOT NULL DEFAULT '',
          model       TEXT NOT NULL DEFAULT 'gpt-4o-mini',
          messages_json TEXT NOT NULL DEFAULT '[]',
          created_at  TEXT NOT NULL,
          updated_at  TEXT NOT NULL
        )
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_gpt_conv_user
        ON gpt_conversations(user_id, updated_at DESC)
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS kling_web_history (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id TEXT NOT NULL,
          item_id TEXT NOT NULL,
          created_at TEXT NOT NULL,
          prompt TEXT NOT NULL DEFAULT '',
          model_id TEXT,
          model_ver TEXT,
          model_label TEXT,
          frame_mode TEXT,
          sound_enabled INTEGER DEFAULT 0,
          settings_json TEXT,
          has_start_frame INTEGER DEFAULT 0,
          has_end_frame INTEGER DEFAULT 0,
          video_urls_json TEXT
        )
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_kling_web_history_user
        ON kling_web_history(user_id, created_at DESC)
    """)

    # ── kling_web_history 마이그레이션 ──
    try:
        cur.execute("ALTER TABLE kling_web_history ADD COLUMN video_urls_json TEXT")
    except Exception:
        pass
    # video_url → video_urls_json 마이그레이션 (기존 단일값 → JSON 배열)
    try:
        cur.execute("""
            UPDATE kling_web_history
            SET video_urls_json = '["' || video_url || '"]'
            WHERE video_url IS NOT NULL AND video_url != '' AND video_urls_json IS NULL
        """)
    except Exception:
        pass

    # ── kling_web_history 마이그레이션: 프레임 이미지 데이터 컬럼 ──
    try:
        cur.execute("ALTER TABLE kling_web_history ADD COLUMN start_frame_data TEXT")
    except Exception:
        pass
    try:
        cur.execute("ALTER TABLE kling_web_history ADD COLUMN end_frame_data TEXT")
    except Exception:
        pass

    cur.execute("""
        CREATE TABLE IF NOT EXISTS elevenlabs_history (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id TEXT NOT NULL,
          item_id TEXT NOT NULL,
          created_at TEXT NOT NULL,
          text TEXT NOT NULL DEFAULT '',
          voice_id TEXT,
          voice_name TEXT,
          model_id TEXT,
          model_label TEXT,
          settings_json TEXT,
          language_override INTEGER DEFAULT 0,
          speaker_boost INTEGER DEFAULT 0,
          audio_url TEXT
        )
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_elevenlabs_history_user
        ON elevenlabs_history(user_id, created_at DESC)
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS nanobanana_history (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id TEXT NOT NULL,
          item_id TEXT NOT NULL,
          created_at TEXT NOT NULL,
          prompt TEXT NOT NULL DEFAULT '',
          model_id TEXT,
          model_label TEXT,
          aspect_ratio TEXT DEFAULT '1:1',
          num_images INTEGER DEFAULT 1,
          style_preset TEXT,
          negative_prompt TEXT DEFAULT '',
          settings_json TEXT,
          image_urls_json TEXT
        )
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_nanobanana_history_user
        ON nanobanana_history(user_id, created_at DESC)
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS nanobanana_sessions (
          id         TEXT PRIMARY KEY,
          user_id    TEXT NOT NULL,
          title      TEXT NOT NULL DEFAULT '',
          model      TEXT NOT NULL DEFAULT 'imagen-4.0-generate-001',
          turns_json TEXT NOT NULL DEFAULT '[]',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        )
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_nanobanana_sessions_user
        ON nanobanana_sessions(user_id, updated_at DESC)
    """)

    # ── chat_messages 테이블 ──
    cur.execute("""
        CREATE TABLE IF NOT EXISTS chat_messages (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          school_id TEXT NOT NULL,
          sender_id TEXT NOT NULL,
          sender_role TEXT NOT NULL,
          message TEXT NOT NULL,
          created_at TEXT NOT NULL
        )
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_chat_messages_school
        ON chat_messages(school_id, created_at DESC)
    """)

    # ── users 테이블 마이그레이션: suno_account_id 컬럼 ──
    try:
        cur.execute("ALTER TABLE users ADD COLUMN suno_account_id INTEGER DEFAULT 0")
    except Exception:
        pass  # 이미 존재

    # ── users.role 마이그레이션: 'user' → 'student' ──
    try:
        cur.execute("UPDATE users SET role='student' WHERE role='user'")
    except Exception:
        pass

    conn.commit()
    conn.close()
    force_sync()  # 스키마 변경을 Turso에 즉시 반영
    _DB_INITIALIZED = True


def insert_run(cfg: AppConfig, row: dict):
    conn = get_db(cfg)
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


def insert_run_and_activate(cfg: AppConfig, row: dict, provider: str, operation: str):
    """insert_run + add_active_job → 단일 커밋."""
    conn = get_db(cfg)
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
    ts = now_iso()
    cur.execute("""
        INSERT OR REPLACE INTO active_jobs (
            run_id, created_at, updated_at, user_id, session_id, provider, operation, state
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        row["run_id"], ts, ts, st.session_state.user_id, st.session_state.session_id,
        provider, operation, "running",
    ))
    conn.commit()
    conn.close()


def update_run(cfg: AppConfig, run_id: str, **fields):
    if not fields:
        return
    conn = get_db(cfg)
    cur = conn.cursor()
    cols = ", ".join([f"{k}=?" for k in fields.keys()])
    vals = list(fields.values())
    vals.append(run_id)
    cur.execute(f"UPDATE runs SET {cols} WHERE run_id=?", vals)
    conn.commit()
    conn.close()


def update_run_and_touch(cfg: AppConfig, run_id: str, active_state: Optional[str] = None, **fields):
    """update_run + touch_active_job → 단일 커밋 (폴링 루프용)."""
    conn = get_db(cfg)
    cur = conn.cursor()
    if fields:
        cols = ", ".join([f"{k}=?" for k in fields.keys()])
        vals = list(fields.values())
        vals.append(run_id)
        cur.execute(f"UPDATE runs SET {cols} WHERE run_id=?", vals)
    ts = now_iso()
    if active_state is None:
        cur.execute("UPDATE active_jobs SET updated_at=? WHERE run_id=?", (ts, run_id))
    else:
        cur.execute("UPDATE active_jobs SET updated_at=?, state=? WHERE run_id=?", (ts, active_state, run_id))
    conn.commit()
    conn.close()


def finish_run(cfg: AppConfig, run_id: str, *, remove_active: bool = True, **fields):
    """update_run + remove_active_job → 단일 커밋 (종료 시)."""
    conn = get_db(cfg)
    cur = conn.cursor()
    if fields:
        cols = ", ".join([f"{k}=?" for k in fields.keys()])
        vals = list(fields.values())
        vals.append(run_id)
        cur.execute(f"UPDATE runs SET {cols} WHERE run_id=?", vals)
    if remove_active:
        cur.execute("DELETE FROM active_jobs WHERE run_id=?", (run_id,))
    conn.commit()
    conn.close()


def remove_active_job(cfg: AppConfig, run_id: str):
    conn = get_db(cfg)
    cur = conn.cursor()
    cur.execute("DELETE FROM active_jobs WHERE run_id=?", (run_id,))
    conn.commit()
    conn.close()


def count_active_jobs(cfg: AppConfig, user_id: Optional[str] = None) -> int:
    conn = get_db(cfg)
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
    conn = get_db(cfg)
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
    conn = get_db(cfg)
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

        conn = get_db(cfg)
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
    conn = get_db(cfg)
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
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS c FROM users")
        return int(cur.fetchone()["c"]) > 0
    finally:
        conn.close()


def get_user(cfg: AppConfig, user_id: str):
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
        return cur.fetchone()
    finally:
        conn.close()


def list_users(cfg: AppConfig, include_inactive: bool = True):
    conn = get_db(cfg)
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
    conn = get_db(cfg)
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


def update_user_fields(cfg: AppConfig, user_id: str, *, role: str | None = None, school_id: str | None = None, suno_account_id: int | None = None):
    """role, school_id, suno_account_id 중 변경할 필드만 업데이트."""
    parts, params = [], []
    if role is not None:
        parts.append("role=?"); params.append(role)
    if school_id is not None:
        parts.append("school_id=?"); params.append(school_id)
    if suno_account_id is not None:
        parts.append("suno_account_id=?"); params.append(suno_account_id)
    if not parts:
        return
    parts.append("updated_at=?"); params.append(now_iso())
    params.append(user_id)
    conn = get_db(cfg)
    try:
        conn.execute(f"UPDATE users SET {', '.join(parts)} WHERE user_id=?", params)
        conn.commit()
    finally:
        conn.close()


def set_user_password(cfg: AppConfig, user_id: str, password_hash: str):
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        cur.execute("UPDATE users SET password_hash=?, updated_at=? WHERE user_id=?", (password_hash, now_iso(), user_id))
        conn.commit()
    finally:
        conn.close()


def set_user_active(cfg: AppConfig, user_id: str, is_active: bool):
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        cur.execute("UPDATE users SET is_active=?, updated_at=? WHERE user_id=?", (1 if is_active else 0, now_iso(), user_id))
        conn.commit()
    finally:
        conn.close()


def hard_delete_user(cfg: AppConfig, user_id: str):
    conn = get_db(cfg)
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
    conn = get_db(cfg)
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
    conn = get_db(cfg)
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
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        cur.execute("UPDATE user_sessions SET last_seen=? WHERE session_token=?", (now_iso(), token))
        conn.commit()
    finally:
        conn.close()


def revoke_user_session(cfg: AppConfig, token: str):
    if not token:
        return
    conn = get_db(cfg)
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
    conn = get_db(cfg)
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
    conn = get_db(cfg)
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
    conn = get_db(cfg)
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
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        cur.execute(
            """SELECT * FROM api_key_leases ORDER BY acquired_at DESC LIMIT ?""",
            (limit,),
        )
        return cur.fetchall()
    finally:
        conn.close()


# ── MJ Gallery ──────────────────────────────────────────

def insert_mj_gallery_item(cfg: AppConfig, user_id: str, item: dict) -> int:
    """MJ 갤러리 아이템 저장. 새 row의 id를 반환한다."""
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO mj_gallery (
                user_id, created_at, display_date, prompt, tags_json,
                aspect_ratio, settings_json, images_json, attached_images_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id,
            now_iso(),
            item.get("date", ""),
            item.get("prompt", ""),
            json.dumps(item.get("tags", []), ensure_ascii=False),
            item.get("aspect_ratio", "1:1"),
            json.dumps(item.get("settings"), ensure_ascii=False) if item.get("settings") else None,
            json.dumps(item.get("images", []), ensure_ascii=False),
            json.dumps(item.get("attached_images"), ensure_ascii=False) if item.get("attached_images") else None,
        ))
        row_id = cur.lastrowid
        conn.commit()
        return row_id
    finally:
        conn.close()


def load_mj_gallery(cfg: AppConfig, user_id: str, limit: int = 200) -> list:
    """사용자별 MJ 갤러리 아이템을 최신순으로 로드한다."""
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, display_date, prompt, tags_json, aspect_ratio,
                   images_json, attached_images_json
            FROM mj_gallery
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT ?
        """, (user_id, limit))
        rows = cur.fetchall()
        items = []
        for r in rows:
            item = {
                "id": r["id"],
                "date": r["display_date"],
                "prompt": r["prompt"],
                "tags": json.loads(r["tags_json"]) if r["tags_json"] else [],
                "aspect_ratio": r["aspect_ratio"] or "1:1",
                "images": json.loads(r["images_json"]) if r["images_json"] else [],
            }
            if r["attached_images_json"]:
                item["attached_images"] = json.loads(r["attached_images_json"])
            items.append(item)
        return items
    finally:
        conn.close()


def update_mj_gallery_images(cfg: AppConfig, item_id: int, images: list):
    """MJ 갤러리 아이템의 images_json 업데이트."""
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE mj_gallery SET images_json = ? WHERE id = ?",
            (json.dumps(images, ensure_ascii=False), item_id),
        )
        conn.commit()
    finally:
        conn.close()


def backfill_mj_gallery_mock_images(cfg: AppConfig):
    """images_json이 비어있는 MJ 갤러리 레코드에 picsum mock 이미지를 채워넣는다."""
    ASPECT_SIZES = {
        "1:1":  (1024, 1024),
        "16:9": (1024, 576),
        "9:16": (576, 1024),
        "4:3":  (1024, 768),
        "3:4":  (768, 1024),
        "3:2":  (1024, 683),
        "2:3":  (683, 1024),
    }
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, aspect_ratio FROM mj_gallery "
            "WHERE images_json IS NULL OR images_json = '' OR images_json = '[]'"
        )
        rows = cur.fetchall()
        if not rows:
            return 0
        counter = 0
        for r in rows:
            ar = r["aspect_ratio"] or "1:1"
            w, h = ASPECT_SIZES.get(ar, (1024, 1024))
            urls = []
            for _ in range(4):
                counter += 1
                urls.append(f"https://picsum.photos/seed/mjfill{counter}/{w}/{h}")
            cur.execute(
                "UPDATE mj_gallery SET images_json = ? WHERE id = ?",
                (json.dumps(urls, ensure_ascii=False), r["id"]),
            )
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def delete_mj_gallery_item(cfg: AppConfig, user_id: str, item_id: int):
    """MJ 갤러리 아이템 삭제 (본인 소유만)."""
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM mj_gallery WHERE id = ? AND user_id = ?", (item_id, user_id))
        conn.commit()
    finally:
        conn.close()


def list_mj_gallery_admin(cfg: AppConfig, limit: int = 200, user_id: str | None = None):
    """관리자용: MJ 갤러리 아이템 전체/유저별 조회. 첨부이미지는 유무만 표시."""
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        sql = """
            SELECT id, user_id, created_at, display_date, prompt,
                   tags_json, aspect_ratio, settings_json, images_json,
                   CASE WHEN attached_images_json IS NOT NULL
                        AND attached_images_json != 'null'
                   THEN 'Y' ELSE '' END AS has_attachments
            FROM mj_gallery
        """
        if user_id:
            sql += " WHERE user_id = ? ORDER BY id ASC LIMIT ?"
            cur.execute(sql, (user_id, limit))
        else:
            sql += " ORDER BY id ASC LIMIT ?"
            cur.execute(sql, (limit,))
        return cur.fetchall()
    finally:
        conn.close()


def get_mj_gallery_by_id(cfg: AppConfig, row_id: int) -> dict | None:
    """MJ 갤러리 아이템 상세 조회 (admin 팝업용)."""
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, user_id, created_at, display_date, prompt,
                   tags_json, aspect_ratio, settings_json,
                   images_json, attached_images_json
            FROM mj_gallery WHERE id = ?
        """, (row_id,))
        r = cur.fetchone()
        if not r:
            return None
        return {
            "id": r["id"],
            "user_id": r["user_id"],
            "created_at": r["created_at"],
            "display_date": r["display_date"],
            "prompt": r["prompt"],
            "tags": json.loads(r["tags_json"]) if r["tags_json"] else [],
            "aspect_ratio": r["aspect_ratio"] or "1:1",
            "settings": json.loads(r["settings_json"]) if r["settings_json"] else {},
            "images": json.loads(r["images_json"]) if r["images_json"] else [],
            "attached_images": json.loads(r["attached_images_json"]) if r["attached_images_json"] else [],
        }
    finally:
        conn.close()


# ── GPT Conversations ────────────────────────────────────

def upsert_gpt_conversation(cfg: AppConfig, user_id: str, conv: dict):
    """GPT 대화 저장/갱신 (INSERT ON CONFLICT UPDATE)."""
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        ts = now_iso()
        cur.execute("""
            INSERT INTO gpt_conversations
                (id, user_id, title, model, messages_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                title=excluded.title,
                model=excluded.model,
                messages_json=excluded.messages_json,
                updated_at=excluded.updated_at
        """, (
            conv["id"],
            user_id,
            conv.get("title", ""),
            conv.get("model", "gpt-4o-mini"),
            json.dumps(conv.get("messages", []), ensure_ascii=False),
            ts, ts,
        ))
        conn.commit()
    finally:
        conn.close()


def load_gpt_conversations(cfg: AppConfig, user_id: str, limit: int = 100) -> list:
    """사용자별 GPT 대화 최신순 로드."""
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, title, model, messages_json, created_at, updated_at
            FROM gpt_conversations
            WHERE user_id = ?
            ORDER BY updated_at DESC
            LIMIT ?
        """, (user_id, limit))
        rows = cur.fetchall()
        result = []
        for r in rows:
            result.append({
                "id": r["id"],
                "title": r["title"],
                "model": r["model"],
                "messages": json.loads(r["messages_json"]) if r["messages_json"] else [],
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            })
        return result
    finally:
        conn.close()


def delete_gpt_conversation(cfg: AppConfig, user_id: str, conv_id: str):
    """GPT 대화 삭제 (소유자 확인)."""
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM gpt_conversations WHERE id = ? AND user_id = ?",
            (conv_id, user_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_user_suno_account_id(cfg: AppConfig, user_id: str) -> int:
    """사용자에게 배정된 Suno 계정 번호 반환 (0 = 미배정)."""
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        cur.execute("SELECT suno_account_id FROM users WHERE user_id = ?", (user_id,))
        row = cur.fetchone()
        return int(row["suno_account_id"]) if row and row["suno_account_id"] else 0
    finally:
        conn.close()



def list_gpt_conversations_admin(
    cfg: AppConfig, limit: int = 200, user_id: str | None = None,
) -> list[dict]:
    """관리자용: GPT 대화 목록 (메시지 내용 제외, 개수만)."""
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        sql = """
            SELECT id, user_id, title, model, messages_json,
                   created_at, updated_at
            FROM gpt_conversations
        """
        if user_id:
            sql += " WHERE user_id = ? ORDER BY id ASC LIMIT ?"
            cur.execute(sql, (user_id, limit))
        else:
            sql += " ORDER BY id ASC LIMIT ?"
            cur.execute(sql, (limit,))
        rows = cur.fetchall()
        result = []
        for r in rows:
            msgs = json.loads(r["messages_json"]) if r["messages_json"] else []
            result.append({
                "id": r["id"],
                "user_id": r["user_id"],
                "title": r["title"],
                "model": r["model"],
                "msg_count": len(msgs),
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            })
        return result
    finally:
        conn.close()


def get_gpt_conversation_by_id(cfg: AppConfig, conv_id: str) -> dict | None:
    """관리자용: 특정 GPT 대화 전체 로드 (messages 포함)."""
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, user_id, title, model, messages_json, created_at, updated_at "
            "FROM gpt_conversations WHERE id = ?",
            (conv_id,),
        )
        r = cur.fetchone()
        if not r:
            return None
        return {
            "id": r["id"],
            "user_id": r["user_id"],
            "title": r["title"],
            "model": r["model"],
            "messages": json.loads(r["messages_json"]) if r["messages_json"] else [],
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
        }
    finally:
        conn.close()


# ── Kling Web History ───────────────────────────────────

def insert_kling_web_item(cfg: AppConfig, user_id: str, item: dict) -> int:
    """Kling 웹 히스토리 아이템 저장. 새 row의 id를 반환한다."""
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO kling_web_history (
                user_id, item_id, created_at, prompt, model_id, model_ver,
                model_label, frame_mode, sound_enabled, settings_json,
                has_start_frame, has_end_frame,
                start_frame_data, end_frame_data
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id,
            item.get("item_id", ""),
            now_iso(),
            item.get("prompt", ""),
            item.get("model_id"),
            item.get("model_ver"),
            item.get("model_label"),
            item.get("frame_mode"),
            1 if item.get("sound_enabled") else 0,
            json.dumps(item.get("settings", {}), ensure_ascii=False),
            1 if item.get("has_start_frame") else 0,
            1 if item.get("has_end_frame") else 0,
            item.get("start_frame_data"),
            item.get("end_frame_data"),
        ))
        row_id = cur.lastrowid
        conn.commit()
        return row_id
    finally:
        conn.close()


def load_kling_web_history(cfg: AppConfig, user_id: str, limit: int = 200) -> list:
    """사용자별 Kling 웹 히스토리를 최신순으로 로드한다."""
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, item_id, prompt, model_id, model_ver, model_label,
                   frame_mode, sound_enabled, settings_json,
                   has_start_frame, has_end_frame, video_urls_json
            FROM kling_web_history
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT ?
        """, (user_id, limit))
        rows = cur.fetchall()
        items = []
        for r in rows:
            items.append({
                "db_id": r["id"],
                "item_id": r["item_id"],
                "prompt": r["prompt"],
                "model_id": r["model_id"],
                "model_ver": r["model_ver"],
                "model_label": r["model_label"],
                "frame_mode": r["frame_mode"],
                "sound_enabled": bool(r["sound_enabled"]),
                "settings": json.loads(r["settings_json"]) if r["settings_json"] else {},
                "has_start_frame": bool(r["has_start_frame"]),
                "has_end_frame": bool(r["has_end_frame"]),
                "video_urls": json.loads(r["video_urls_json"]) if r["video_urls_json"] else [],
                "loading": False,
            })
        return items
    finally:
        conn.close()


def update_kling_web_video_urls(cfg: AppConfig, item_id: str, video_urls: list):
    """Kling 히스토리 아이템의 video_urls 업데이트."""
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE kling_web_history SET video_urls_json = ? WHERE item_id = ?",
            (json.dumps(video_urls, ensure_ascii=False), item_id),
        )
        conn.commit()
    finally:
        conn.close()


def list_kling_web_admin(
    cfg: AppConfig, limit: int = 200, user_id: str | None = None,
) -> list[dict]:
    """관리자용: Kling 웹 히스토리 목록 조회."""
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        base = """
            SELECT id, user_id, item_id, created_at, prompt,
                   model_label, frame_mode, sound_enabled,
                   settings_json, has_start_frame, has_end_frame,
                   video_urls_json
            FROM kling_web_history
        """
        if user_id:
            base += " WHERE user_id = ? ORDER BY id ASC LIMIT ?"
            cur.execute(base, (user_id, limit))
        else:
            base += " ORDER BY id ASC LIMIT ?"
            cur.execute(base, (limit,))
        rows = cur.fetchall()
        items = []
        for r in rows:
            stg = json.loads(r["settings_json"]) if r["settings_json"] else {}
            urls = json.loads(r["video_urls_json"]) if r["video_urls_json"] else []
            items.append({
                "id": r["id"],
                "user_id": r["user_id"],
                "created_at": r["created_at"],
                "prompt": (r["prompt"] or "")[:80],
                "model": r["model_label"] or "",
                "resolution": stg.get("resolution", ""),
                "duration": stg.get("duration", ""),
                "count": stg.get("count", "1"),
                "sound": "ON" if r["sound_enabled"] else "",
                "videos": len(urls),
                "start_img": "O" if r["has_start_frame"] else "",
                "end_img": "O" if r["has_end_frame"] else "",
            })
        return items
    finally:
        conn.close()


def get_kling_web_by_id(cfg: AppConfig, row_id: int) -> dict | None:
    """Kling 웹 히스토리 아이템 상세 조회 (admin 팝업용)."""
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, user_id, item_id, created_at, prompt,
                   model_id, model_ver, model_label, frame_mode,
                   sound_enabled, settings_json,
                   has_start_frame, has_end_frame, video_urls_json,
                   start_frame_data, end_frame_data
            FROM kling_web_history WHERE id = ?
        """, (row_id,))
        r = cur.fetchone()
        if not r:
            return None
        return {
            "id": r["id"],
            "user_id": r["user_id"],
            "item_id": r["item_id"],
            "created_at": r["created_at"],
            "prompt": r["prompt"],
            "model_id": r["model_id"],
            "model_ver": r["model_ver"],
            "model_label": r["model_label"],
            "frame_mode": r["frame_mode"],
            "sound_enabled": bool(r["sound_enabled"]),
            "settings": json.loads(r["settings_json"]) if r["settings_json"] else {},
            "has_start_frame": bool(r["has_start_frame"]),
            "has_end_frame": bool(r["has_end_frame"]),
            "video_urls": json.loads(r["video_urls_json"]) if r["video_urls_json"] else [],
            "start_frame_data": r["start_frame_data"],
            "end_frame_data": r["end_frame_data"],
        }
    finally:
        conn.close()


# ──────────────────────────────────────
# ElevenLabs TTS History
# ──────────────────────────────────────

def insert_elevenlabs_item(cfg: AppConfig, user_id: str, item: dict) -> int:
    """ElevenLabs TTS 히스토리 아이템 저장. 새 row의 id를 반환한다."""
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO elevenlabs_history (
                user_id, item_id, created_at, text, voice_id, voice_name,
                model_id, model_label, settings_json,
                language_override, speaker_boost
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id,
            item.get("item_id", ""),
            now_iso(),
            item.get("text", ""),
            item.get("voice_id"),
            item.get("voice_name"),
            item.get("model_id"),
            item.get("model_label"),
            json.dumps(item.get("settings", {}), ensure_ascii=False),
            1 if item.get("language_override") else 0,
            1 if item.get("speaker_boost") else 0,
        ))
        row_id = cur.lastrowid
        conn.commit()
        return row_id
    finally:
        conn.close()


def load_elevenlabs_history(cfg: AppConfig, user_id: str, limit: int = 200) -> list:
    """사용자별 ElevenLabs TTS 히스토리를 최신순으로 로드한다."""
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, item_id, text, voice_id, voice_name,
                   model_id, model_label, settings_json,
                   language_override, speaker_boost, audio_url
            FROM elevenlabs_history
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT ?
        """, (user_id, limit))
        rows = cur.fetchall()
        items = []
        for r in rows:
            items.append({
                "db_id": r["id"],
                "item_id": r["item_id"],
                "text": r["text"],
                "voice_id": r["voice_id"],
                "voice_name": r["voice_name"],
                "model_id": r["model_id"],
                "model_label": r["model_label"],
                "settings": json.loads(r["settings_json"]) if r["settings_json"] else {},
                "language_override": bool(r["language_override"]),
                "speaker_boost": bool(r["speaker_boost"]),
                "audio_url": r["audio_url"],
                "loading": False,
            })
        return items
    finally:
        conn.close()


def update_elevenlabs_audio_url(cfg: AppConfig, item_id: str, audio_url: str):
    """ElevenLabs 히스토리 아이템의 audio_url 업데이트."""
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE elevenlabs_history SET audio_url = ? WHERE item_id = ?",
            (audio_url, item_id),
        )
        conn.commit()
    finally:
        conn.close()


def list_elevenlabs_admin(cfg: AppConfig, limit: int = 200, user_id: str | None = None) -> list[dict]:
    """관리자용: ElevenLabs TTS 히스토리 목록 조회."""
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        base = """
            SELECT id, user_id, item_id, created_at, text,
                   voice_name, model_label, settings_json,
                   language_override, speaker_boost, audio_url
            FROM elevenlabs_history
        """
        if user_id:
            base += " WHERE user_id = ? ORDER BY id ASC LIMIT ?"
            cur.execute(base, (user_id, limit))
        else:
            base += " ORDER BY id ASC LIMIT ?"
            cur.execute(base, (limit,))
        rows = cur.fetchall()
        items = []
        for r in rows:
            items.append({
                "id": r["id"],
                "user_id": r["user_id"],
                "created_at": r["created_at"],
                "text": (r["text"] or "")[:80],
                "voice": r["voice_name"] or "",
                "model": r["model_label"] or "",
                "has_audio": "O" if r["audio_url"] else "",
            })
        return items
    finally:
        conn.close()


def get_elevenlabs_by_id(cfg: AppConfig, row_id: int) -> dict | None:
    """ElevenLabs TTS 히스토리 아이템 상세 조회 (admin 팝업용)."""
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, user_id, item_id, created_at, text,
                   voice_id, voice_name, model_id, model_label,
                   settings_json, language_override, speaker_boost,
                   audio_url
            FROM elevenlabs_history WHERE id = ?
        """, (row_id,))
        r = cur.fetchone()
        if not r:
            return None
        return {
            "id": r["id"],
            "user_id": r["user_id"],
            "item_id": r["item_id"],
            "created_at": r["created_at"],
            "text": r["text"],
            "voice_id": r["voice_id"],
            "voice_name": r["voice_name"],
            "model_id": r["model_id"],
            "model_label": r["model_label"],
            "settings": json.loads(r["settings_json"]) if r["settings_json"] else {},
            "language_override": bool(r["language_override"]),
            "speaker_boost": bool(r["speaker_boost"]),
            "audio_url": r["audio_url"],
        }
    finally:
        conn.close()


# ══════════════════════════════════════
# NanoBanana (Google Imagen)
# ══════════════════════════════════════

def insert_nanobanana_item(cfg: AppConfig, user_id: str, item: dict) -> int:
    """NanoBanana 이미지 생성 히스토리 아이템 저장."""
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO nanobanana_history (
                user_id, item_id, created_at, prompt, model_id, model_label,
                aspect_ratio, num_images, style_preset, negative_prompt,
                settings_json, image_urls_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id,
            item.get("item_id", ""),
            now_iso(),
            item.get("prompt", ""),
            item.get("model_id"),
            item.get("model_label"),
            item.get("aspect_ratio", "1:1"),
            item.get("num_images", 1),
            item.get("style_preset"),
            item.get("negative_prompt", ""),
            json.dumps(item.get("settings", {}), ensure_ascii=False),
            json.dumps(item.get("image_urls", []), ensure_ascii=False),
        ))
        row_id = cur.lastrowid
        conn.commit()
        return row_id
    finally:
        conn.close()


def load_nanobanana_history(cfg: AppConfig, user_id: str, limit: int = 200) -> list:
    """사용자별 NanoBanana 히스토리를 최신순으로 로드."""
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, item_id, prompt, model_id, model_label,
                   aspect_ratio, num_images, style_preset, negative_prompt,
                   settings_json, image_urls_json
            FROM nanobanana_history
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT ?
        """, (user_id, limit))
        rows = cur.fetchall()
        items = []
        for r in rows:
            items.append({
                "db_id": r["id"],
                "item_id": r["item_id"],
                "prompt": r["prompt"],
                "model_id": r["model_id"],
                "model_label": r["model_label"],
                "aspect_ratio": r["aspect_ratio"] or "1:1",
                "num_images": r["num_images"] or 1,
                "style_preset": r["style_preset"],
                "negative_prompt": r["negative_prompt"] or "",
                "settings": json.loads(r["settings_json"]) if r["settings_json"] else {},
                "image_urls": json.loads(r["image_urls_json"]) if r["image_urls_json"] else [],
                "loading": False,
            })
        return items
    finally:
        conn.close()


def update_nanobanana_image_urls(cfg: AppConfig, item_id: str, image_urls: list):
    """NanoBanana 히스토리 아이템의 image_urls 업데이트."""
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE nanobanana_history SET image_urls_json = ? WHERE item_id = ?",
            (json.dumps(image_urls, ensure_ascii=False), item_id),
        )
        conn.commit()
    finally:
        conn.close()


def list_nanobanana_admin(cfg: AppConfig, limit: int = 200, user_id: str | None = None) -> list[dict]:
    """관리자용: NanoBanana 히스토리 목록 조회."""
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        base = """
            SELECT id, user_id, item_id, created_at, prompt,
                   model_label, aspect_ratio, num_images,
                   style_preset, image_urls_json
            FROM nanobanana_history
        """
        if user_id:
            base += " WHERE user_id = ? ORDER BY id ASC LIMIT ?"
            cur.execute(base, (user_id, limit))
        else:
            base += " ORDER BY id ASC LIMIT ?"
            cur.execute(base, (limit,))
        rows = cur.fetchall()
        items = []
        for r in rows:
            urls = json.loads(r["image_urls_json"]) if r["image_urls_json"] else []
            items.append({
                "id": r["id"],
                "user_id": r["user_id"],
                "created_at": r["created_at"],
                "prompt": (r["prompt"] or "")[:80],
                "model": r["model_label"] or "",
                "aspect_ratio": r["aspect_ratio"] or "1:1",
                "images": len(urls),
                "style": r["style_preset"] or "",
            })
        return items
    finally:
        conn.close()


def get_nanobanana_by_id(cfg: AppConfig, row_id: int) -> dict | None:
    """NanoBanana 히스토리 아이템 상세 조회 (admin 팝업용)."""
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, user_id, item_id, created_at, prompt,
                   model_id, model_label, aspect_ratio, num_images,
                   style_preset, negative_prompt,
                   settings_json, image_urls_json
            FROM nanobanana_history WHERE id = ?
        """, (row_id,))
        r = cur.fetchone()
        if not r:
            return None
        return {
            "id": r["id"],
            "user_id": r["user_id"],
            "item_id": r["item_id"],
            "created_at": r["created_at"],
            "prompt": r["prompt"],
            "model_id": r["model_id"],
            "model_label": r["model_label"],
            "aspect_ratio": r["aspect_ratio"] or "1:1",
            "num_images": r["num_images"] or 1,
            "style_preset": r["style_preset"],
            "negative_prompt": r["negative_prompt"] or "",
            "settings": json.loads(r["settings_json"]) if r["settings_json"] else {},
            "image_urls": json.loads(r["image_urls_json"]) if r["image_urls_json"] else [],
        }
    finally:
        conn.close()


# ── NanoBanana Sessions (멀티턴 편집) ──────────────────────


def upsert_nanobanana_session(cfg: AppConfig, user_id: str, session: dict):
    """NanoBanana 세션 저장/갱신 (INSERT ON CONFLICT UPDATE)."""
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        ts = now_iso()
        cur.execute("""
            INSERT INTO nanobanana_sessions
                (id, user_id, title, model, turns_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                title=excluded.title,
                model=excluded.model,
                turns_json=excluded.turns_json,
                updated_at=excluded.updated_at
        """, (
            session["id"],
            user_id,
            session.get("title", ""),
            session.get("model", "imagen-4.0-generate-001"),
            json.dumps(session.get("turns", []), ensure_ascii=False),
            ts, ts,
        ))
        conn.commit()
    finally:
        conn.close()


def load_nanobanana_sessions(cfg: AppConfig, user_id: str, limit: int = 100) -> list:
    """사용자별 NanoBanana 세션 최신순 로드."""
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, title, model, turns_json, created_at, updated_at
            FROM nanobanana_sessions
            WHERE user_id = ?
            ORDER BY updated_at DESC
            LIMIT ?
        """, (user_id, limit))
        rows = cur.fetchall()
        result = []
        for r in rows:
            result.append({
                "id": r["id"],
                "title": r["title"],
                "model": r["model"],
                "turns": json.loads(r["turns_json"]) if r["turns_json"] else [],
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            })
        return result
    finally:
        conn.close()


def delete_nanobanana_session(cfg: AppConfig, user_id: str, session_id: str):
    """NanoBanana 세션 삭제 (소유자 확인)."""
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM nanobanana_sessions WHERE id = ? AND user_id = ?",
            (session_id, user_id),
        )
        conn.commit()
    finally:
        conn.close()


def list_nanobanana_sessions_admin(
    cfg: AppConfig, limit: int = 200, user_id: str | None = None,
) -> list[dict]:
    """관리자용: NanoBanana 세션 목록 (턴 개수·이미지 수 요약)."""
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        sql = """
            SELECT id, user_id, title, model, turns_json,
                   created_at, updated_at
            FROM nanobanana_sessions
        """
        if user_id:
            sql += " WHERE user_id = ? ORDER BY updated_at DESC LIMIT ?"
            cur.execute(sql, (user_id, limit))
        else:
            sql += " ORDER BY updated_at DESC LIMIT ?"
            cur.execute(sql, (limit,))
        rows = cur.fetchall()
        result = []
        for r in rows:
            turns = json.loads(r["turns_json"]) if r["turns_json"] else []
            total_images = sum(len(t.get("image_urls", [])) for t in turns)
            result.append({
                "id": r["id"],
                "user_id": r["user_id"],
                "title": r["title"],
                "model": r["model"],
                "turn_count": len(turns),
                "total_images": total_images,
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            })
        return result
    finally:
        conn.close()


def get_nanobanana_session_by_id(cfg: AppConfig, session_id: str) -> dict | None:
    """관리자용: 특정 NanoBanana 세션 전체 로드 (turns 포함)."""
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, user_id, title, model, turns_json, created_at, updated_at "
            "FROM nanobanana_sessions WHERE id = ?",
            (session_id,),
        )
        r = cur.fetchone()
        if not r:
            return None
        return {
            "id": r["id"],
            "user_id": r["user_id"],
            "title": r["title"],
            "model": r["model"],
            "turns": json.loads(r["turns_json"]) if r["turns_json"] else [],
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
        }
    finally:
        conn.close()


# ----------------------------
# Chat Messages
# ----------------------------

def insert_chat_message(cfg: AppConfig, school_id: str, sender_id: str, sender_role: str, message: str):
    conn = get_db(cfg)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO chat_messages (school_id, sender_id, sender_role, message, created_at)
        VALUES (?, ?, ?, ?, ?)
    """, (school_id, sender_id, sender_role, message, now_iso()))
    conn.commit()
    conn.close()


def load_chat_messages(cfg: AppConfig, school_id: str, limit: int = 100) -> list[dict]:
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, school_id, sender_id, sender_role, message, created_at
            FROM chat_messages
            WHERE school_id = ?
            ORDER BY created_at DESC
            LIMIT ?
        """, (school_id, limit))
        rows = cur.fetchall()
        return [dict(r) for r in reversed(rows)]
    finally:
        conn.close()


def list_chat_messages_admin(cfg: AppConfig, limit: int = 200, school_id: str | None = None) -> list[dict]:
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        if school_id:
            cur.execute("""
                SELECT id, school_id, sender_id, sender_role, message, created_at
                FROM chat_messages
                WHERE school_id = ?
                ORDER BY created_at DESC
                LIMIT ?
            """, (school_id, limit))
        else:
            cur.execute("""
                SELECT id, school_id, sender_id, sender_role, message, created_at
                FROM chat_messages
                ORDER BY created_at DESC
                LIMIT ?
            """, (limit,))
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()
