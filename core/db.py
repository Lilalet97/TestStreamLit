# core/db.py
from datetime import datetime, timedelta
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
    # runs: 레거시 — LEGACY_TABLES에서 관리, init_db에서 더 이상 생성하지 않음

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

    # nanobanana_history: 레거시 — LEGACY_TABLES에서 관리, init_db에서 더 이상 생성하지 않음

    cur.execute("""
        CREATE TABLE IF NOT EXISTS nanobanana_sessions (
          id         TEXT PRIMARY KEY,
          user_id    TEXT NOT NULL,
          title      TEXT NOT NULL DEFAULT '',
          model      TEXT NOT NULL DEFAULT 'imagen-4.0-generate-001',
          turns_json TEXT NOT NULL DEFAULT '[]',
          tab_id     TEXT NOT NULL DEFAULT 'nanobanana',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        )
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_nanobanana_sessions_user
        ON nanobanana_sessions(user_id, updated_at DESC)
    """)
    # tab_id 컬럼 마이그레이션 (기존 DB 호환)
    try:
        cur.execute("ALTER TABLE nanobanana_sessions ADD COLUMN tab_id TEXT NOT NULL DEFAULT 'nanobanana'")
    except Exception:
        pass  # 이미 존재

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

    # ── 부하 테스트 ──
    cur.execute("""
        CREATE TABLE IF NOT EXISTS stress_test_runs (
            test_id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            admin_user_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            config_json TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            summary_json TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS stress_test_samples (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            test_id TEXT NOT NULL,
            worker_id INTEGER NOT NULL,
            request_seq INTEGER NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT NOT NULL,
            duration_ms INTEGER NOT NULL,
            phase TEXT NOT NULL,
            status TEXT NOT NULL,
            error_text TEXT,
            provider TEXT,
            key_name TEXT,
            FOREIGN KEY(test_id) REFERENCES stress_test_runs(test_id)
        )
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_stress_samples_test
        ON stress_test_samples(test_id, started_at)
    """)

    # ── admin_settings (key-value 설정 저장) ──
    cur.execute("""
        CREATE TABLE IF NOT EXISTS admin_settings (
            key       TEXT PRIMARY KEY,
            value     TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)

    # user_credits: 레거시 — LEGACY_TABLES에서 관리, init_db에서 더 이상 생성하지 않음

    # ── user_balance (통합 크레딧 잔액) ──
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_balance (
            user_id    TEXT PRIMARY KEY,
            balance    INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        )
    """)

    # ── credit_usage_log (크레딧 차감 내역) ──
    cur.execute("""
        CREATE TABLE IF NOT EXISTS credit_usage_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     TEXT NOT NULL,
            school_id   TEXT NOT NULL,
            tab_id      TEXT NOT NULL,
            amount      INTEGER NOT NULL,
            created_at  TEXT NOT NULL
        )
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_credit_usage_school_date
        ON credit_usage_log(school_id, created_at)
    """)

    # ── stress_test_runs 마이그레이션: plan_id, round_label ──
    try:
        cur.execute("ALTER TABLE stress_test_runs ADD COLUMN plan_id TEXT")
    except Exception:
        pass
    try:
        cur.execute("ALTER TABLE stress_test_runs ADD COLUMN round_label TEXT")
    except Exception:
        pass

    # ── kling 크레딧 통합: kling_veo / kling_grok → kling ──
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='user_credits'")
    _has_user_credits = cur.fetchone() is not None
    if _has_user_credits:
        cur.execute("DELETE FROM user_credits WHERE tab_id IN ('kling_veo', 'kling_grok')")
    cur.execute("""
        DELETE FROM admin_settings
        WHERE key LIKE 'credit_cost.kling_veo' OR key LIKE 'credit_cost.kling_grok'
           OR key LIKE 'credit_default.kling_veo' OR key LIKE 'credit_default.kling_grok'
    """)

    # ── user_credits → user_balance 마이그레이션 ──
    cur.execute("SELECT COUNT(*) AS cnt FROM user_balance")
    if cur.fetchone()["cnt"] == 0 and _has_user_credits:
        cur.execute("""
            INSERT OR IGNORE INTO user_balance (user_id, balance, updated_at)
            SELECT user_id, SUM(balance), MAX(updated_at)
            FROM user_credits GROUP BY user_id
        """)

    # ── class_schedules (수업 시간표) ──
    cur.execute("""
        CREATE TABLE IF NOT EXISTS class_schedules (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          school_id TEXT NOT NULL,
          day_of_week INTEGER NOT NULL,
          start_hour INTEGER NOT NULL,
          start_minute INTEGER NOT NULL DEFAULT 0,
          end_hour INTEGER NOT NULL,
          end_minute INTEGER NOT NULL DEFAULT 0,
          label TEXT NOT NULL DEFAULT '',
          color TEXT NOT NULL DEFAULT '#6366f1',
          created_at TEXT,
          updated_at TEXT
        )
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_class_schedules_school_day
        ON class_schedules(school_id, day_of_week)
    """)

    # ── 알림 (notices) ──
    cur.execute("""
        CREATE TABLE IF NOT EXISTS notices (
          notice_id INTEGER PRIMARY KEY AUTOINCREMENT,
          message TEXT NOT NULL,
          target_school TEXT,
          is_active INTEGER NOT NULL DEFAULT 1,
          created_at TEXT,
          expires_at TEXT
        )
    """)

    # ── 서버 점검 (maintenance) ──
    cur.execute("""
        CREATE TABLE IF NOT EXISTS maintenance_schedule (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          scheduled_at TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'scheduled',
          message TEXT NOT NULL DEFAULT '서버 점검이 예정되어 있습니다.',
          created_at TEXT
        )
    """)

    conn.commit()
    conn.close()
    force_sync()  # 스키마 변경을 Turso에 즉시 반영
    _DB_INITIALIZED = True


_NOTICE_TABLES_ENSURED = False

def ensure_notice_tables(cfg: AppConfig):
    """notices / maintenance_schedule 테이블이 없으면 생성.

    init_db()가 이미 실행된 프로세스에서 코드가 업데이트된 경우를 대비한
    경량 마이그레이션 헬퍼. 세션당 1회만 실행.
    """
    global _NOTICE_TABLES_ENSURED
    if _NOTICE_TABLES_ENSURED:
        return
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS notices (
              notice_id INTEGER PRIMARY KEY AUTOINCREMENT,
              message TEXT NOT NULL,
              target_school TEXT,
              is_active INTEGER NOT NULL DEFAULT 1,
              created_at TEXT,
              expires_at TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS maintenance_schedule (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              scheduled_at TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'scheduled',
              message TEXT NOT NULL DEFAULT '서버 점검이 예정되어 있습니다.',
              created_at TEXT
            )
        """)
        conn.commit()
    finally:
        conn.close()
    _NOTICE_TABLES_ENSURED = True


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
        cur.execute("DELETE FROM user_credits WHERE user_id=?", (user_id,))
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
            conv.get("model", cfg.openai_model),
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
                   has_start_frame, has_end_frame,
                   start_frame_data, end_frame_data,
                   video_urls_json
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
                "start_frame_data": r["start_frame_data"],
                "end_frame_data": r["end_frame_data"],
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


def load_nanobanana_history(cfg: AppConfig, user_id: str, limit: int = 200) -> list:
    """사용자별 NanoBanana 이미지를 세션에서 추출하여 최신순 반환 (갤러리용)."""
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, model, turns_json, updated_at
            FROM nanobanana_sessions
            WHERE user_id = ? AND turns_json IS NOT NULL AND turns_json != '[]'
            ORDER BY updated_at DESC
            LIMIT ?
        """, (user_id, limit))
        rows = cur.fetchall()
        items = []
        for r in rows:
            turns = json.loads(r["turns_json"]) if r["turns_json"] else []
            for turn in turns:
                urls = turn.get("image_urls") or []
                if not urls:
                    continue
                items.append({
                    "prompt": turn.get("prompt", ""),
                    "model_label": turn.get("model_label", r["model"] or ""),
                    "aspect_ratio": turn.get("aspect_ratio", "1:1"),
                    "image_urls": urls,
                })
        return items[:limit]
    finally:
        conn.close()




# ── NanoBanana Sessions (멀티턴 편집) ──────────────────────


def upsert_nanobanana_session(cfg: AppConfig, user_id: str, session: dict, tab_id: str = "nanobanana"):
    """NanoBanana 세션 저장/갱신 (INSERT ON CONFLICT UPDATE)."""
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        ts = now_iso()
        cur.execute("""
            INSERT INTO nanobanana_sessions
                (id, user_id, title, model, turns_json, tab_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
            tab_id,
            ts, ts,
        ))
        conn.commit()
    finally:
        conn.close()


def load_nanobanana_sessions(cfg: AppConfig, user_id: str, limit: int = 100, tab_id: str = "nanobanana") -> list:
    """사용자별 NanoBanana 세션 최신순 로드."""
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, title, model, turns_json, created_at, updated_at
            FROM nanobanana_sessions
            WHERE user_id = ? AND tab_id = ?
            ORDER BY updated_at DESC
            LIMIT ?
        """, (user_id, tab_id, limit))
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


# ── DB 관리: Admin Settings + Purge ─────────────────────

PURGEABLE_TABLES = [
    {"key": "stress_test",        "table": "stress_test_runs",    "label": "API — 부하테스트",       "date_col": "created_at",
     "child_table": "stress_test_samples", "fk_col": "test_id", "parent_pk": "test_id"},
    {"key": "mj_gallery",         "table": "mj_gallery",          "label": "Midjourney — 이미지",    "date_col": "created_at"},
    {"key": "gpt_conversations",  "table": "gpt_conversations",   "label": "GPT — 대화 기록",       "date_col": "created_at"},
    {"key": "kling_web_history",  "table": "kling_web_history",   "label": "Kling — 비디오 기록",   "date_col": "created_at"},
    {"key": "elevenlabs_history", "table": "elevenlabs_history",  "label": "ElevenLabs — TTS 기록", "date_col": "created_at"},
    {"key": "nanobanana_sessions","table": "nanobanana_sessions", "label": "NanoBanana — 이미지 세션","date_col": "created_at"},
    {"key": "chat_messages",      "table": "chat_messages",       "label": "채팅 — 메시지 기록",    "date_col": "created_at"},
]


def get_admin_setting(cfg: AppConfig, key: str, default: str = "") -> str:
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        cur.execute("SELECT value FROM admin_settings WHERE key = ?", (key,))
        row = cur.fetchone()
        return row["value"] if row else default
    finally:
        conn.close()


def set_admin_setting(cfg: AppConfig, key: str, value: str):
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO admin_settings (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
        """, (key, value, now_iso()))
        conn.commit()
    finally:
        conn.close()


def get_all_admin_settings(cfg: AppConfig, prefix: str = "") -> dict:
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        if prefix:
            cur.execute(
                "SELECT key, value FROM admin_settings WHERE key LIKE ?",
                (prefix + "%",),
            )
        else:
            cur.execute("SELECT key, value FROM admin_settings")
        return {row["key"]: row["value"] for row in cur.fetchall()}
    finally:
        conn.close()


def get_table_row_counts(cfg: AppConfig) -> dict:
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        counts = {}
        for tbl in PURGEABLE_TABLES:
            cur.execute(f"SELECT COUNT(*) AS c FROM {tbl['table']}")
            counts[tbl["key"]] = int(cur.fetchone()["c"])
            if "child_table" in tbl:
                cur.execute(f"SELECT COUNT(*) AS c FROM {tbl['child_table']}")
                counts[tbl["key"] + "_child"] = int(cur.fetchone()["c"])
        return counts
    finally:
        conn.close()


def count_old_rows(cfg: AppConfig, table_key: str, older_than_days: int) -> int:
    tbl = next((t for t in PURGEABLE_TABLES if t["key"] == table_key), None)
    if not tbl or older_than_days <= 0:
        return 0
    cutoff = (datetime.utcnow() - timedelta(days=older_than_days)).isoformat() + "Z"
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        cur.execute(
            f"SELECT COUNT(*) AS c FROM {tbl['table']} WHERE {tbl['date_col']} < ?",
            (cutoff,),
        )
        return int(cur.fetchone()["c"])
    finally:
        conn.close()


def purge_old_records(cfg: AppConfig, table_key: str, older_than_days: int) -> int:
    tbl = next((t for t in PURGEABLE_TABLES if t["key"] == table_key), None)
    if not tbl or older_than_days <= 0:
        return 0
    cutoff = (datetime.utcnow() - timedelta(days=older_than_days)).isoformat() + "Z"
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        # 삭제 대상 수 미리 조회
        cur.execute(
            f"SELECT COUNT(*) AS c FROM {tbl['table']} WHERE {tbl['date_col']} < ?",
            (cutoff,),
        )
        total = int(cur.fetchone()["c"])
        if total == 0:
            return 0

        # child table 연쇄 삭제
        if "child_table" in tbl:
            cur.execute(
                f"SELECT {tbl['parent_pk']} FROM {tbl['table']} WHERE {tbl['date_col']} < ?",
                (cutoff,),
            )
            parent_ids = [row[tbl["parent_pk"]] for row in cur.fetchall()]
            for pid in parent_ids:
                cur.execute(
                    f"DELETE FROM {tbl['child_table']} WHERE {tbl['fk_col']} = ?",
                    (pid,),
                )

        cur.execute(
            f"DELETE FROM {tbl['table']} WHERE {tbl['date_col']} < ?",
            (cutoff,),
        )
        conn.commit()
        return total
    finally:
        conn.close()


def run_auto_purge(cfg: AppConfig) -> dict:
    settings = get_all_admin_settings(cfg, prefix="purge_days.")
    results = {}
    for tbl in PURGEABLE_TABLES:
        days_str = settings.get(f"purge_days.{tbl['key']}", "0")
        days = int(days_str) if days_str.isdigit() else 0
        if days > 0:
            deleted = purge_old_records(cfg, tbl["key"], days)
            if deleted > 0:
                results[tbl["key"]] = deleted
    return results


# ── 레거시(미사용) 테이블 ─────────────────────

LEGACY_TABLES = [
    {"table": "runs",               "label": "API — 호출 기록 (레거시)",          "reason": "사용되지 않는 API 로깅 테이블"},
    {"table": "nanobanana_history", "label": "NanoBanana — 이미지 기록 (레거시)", "reason": "이미지 데이터가 nanobanana_sessions.turns_json에 통합됨"},
    {"table": "user_credits",      "label": "크레딧 — 탭별 잔액 (레거시)",       "reason": "user_balance 테이블로 마이그레이션 완료"},
]


def list_legacy_tables(cfg: AppConfig) -> list:
    """DB에 실제 존재하는 레거시 테이블 목록 반환."""
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        found = []
        for lt in LEGACY_TABLES:
            cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (lt["table"],),
            )
            if cur.fetchone():
                cur.execute(f"SELECT COUNT(*) AS c FROM {lt['table']}")
                cnt = int(cur.fetchone()["c"])
                found.append({**lt, "row_count": cnt})
        return found
    finally:
        conn.close()


def drop_legacy_tables(cfg: AppConfig) -> list:
    """레거시 테이블을 모두 DROP하고 삭제된 테이블명 목록 반환."""
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        dropped = []
        for lt in LEGACY_TABLES:
            cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (lt["table"],),
            )
            if cur.fetchone():
                cur.execute(f"DROP TABLE {lt['table']}")
                dropped.append(lt["table"])
        conn.commit()
        return dropped
    finally:
        conn.close()
        if dropped:
            force_sync()


def reset_all_data(cfg: AppConfig) -> dict:
    """모든 데이터 테이블의 레코드를 삭제 (스키마 유지). 시스템 테이블 제외."""
    # 데이터 테이블만 삭제 (users, admin_settings 등 시스템 테이블 제외)
    DATA_TABLES = [
        "active_jobs",
        "stress_test_samples", "stress_test_runs",
        "mj_gallery", "gpt_conversations",
        "kling_web_history", "elevenlabs_history",
        "nanobanana_sessions",
        "chat_messages",
        "credit_usage_log",
        "notices", "maintenance_schedule",
    ]
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        result = {}
        for table in DATA_TABLES:
            cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            )
            if cur.fetchone():
                cur.execute(f"SELECT COUNT(*) AS c FROM {table}")
                cnt = int(cur.fetchone()["c"])
                cur.execute(f"DELETE FROM {table}")
                result[table] = cnt
        conn.commit()
        return result
    finally:
        conn.close()
        force_sync()


# ── School Gallery (학교 공유 갤러리) ─────────────────────

def load_school_mj_gallery(cfg: AppConfig, school_id: str, limit: int = 200) -> list:
    """같은 학교 학생들의 MJ 갤러리 (최신순)."""
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT mg.user_id, mg.display_date, mg.prompt,
                   mg.aspect_ratio, mg.images_json, mg.created_at
            FROM mj_gallery mg
            JOIN users u ON mg.user_id = u.user_id
            WHERE u.school_id = ? AND mg.images_json IS NOT NULL
                  AND mg.images_json != '[]'
            ORDER BY mg.created_at DESC
            LIMIT ?
        """, (school_id, limit))
        rows = cur.fetchall()
        items = []
        for r in rows:
            images = json.loads(r["images_json"]) if r["images_json"] else []
            if not images:
                continue
            items.append({
                "user_id": r["user_id"],
                "prompt": r["prompt"],
                "aspect_ratio": r["aspect_ratio"] or "1:1",
                "images": images,
                "date": r["display_date"],
                "created_at": r["created_at"],
            })
        return items
    finally:
        conn.close()


def load_school_kling_gallery(cfg: AppConfig, school_id: str, limit: int = 200) -> list:
    """같은 학교 학생들의 Kling 비디오 (최신순)."""
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT kh.user_id, kh.prompt, kh.model_label,
                   kh.video_urls_json, kh.created_at
            FROM kling_web_history kh
            JOIN users u ON kh.user_id = u.user_id
            WHERE u.school_id = ? AND kh.video_urls_json IS NOT NULL
                  AND kh.video_urls_json != '[]'
            ORDER BY kh.created_at DESC
            LIMIT ?
        """, (school_id, limit))
        rows = cur.fetchall()
        items = []
        for r in rows:
            urls = json.loads(r["video_urls_json"]) if r["video_urls_json"] else []
            if not urls:
                continue
            items.append({
                "user_id": r["user_id"],
                "prompt": r["prompt"],
                "model_label": r["model_label"],
                "video_urls": urls,
                "created_at": r["created_at"],
            })
        return items
    finally:
        conn.close()


def load_school_elevenlabs_gallery(cfg: AppConfig, school_id: str, limit: int = 200) -> list:
    """같은 학교 학생들의 ElevenLabs 오디오 (최신순)."""
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT eh.user_id, eh.text, eh.voice_name,
                   eh.model_label, eh.audio_url, eh.created_at
            FROM elevenlabs_history eh
            JOIN users u ON eh.user_id = u.user_id
            WHERE u.school_id = ? AND eh.audio_url IS NOT NULL
                  AND eh.audio_url != ''
            ORDER BY eh.created_at DESC
            LIMIT ?
        """, (school_id, limit))
        rows = cur.fetchall()
        items = []
        for r in rows:
            if not r["audio_url"]:
                continue
            items.append({
                "user_id": r["user_id"],
                "text": r["text"],
                "voice_name": r["voice_name"],
                "model_label": r["model_label"],
                "audio_url": r["audio_url"],
                "created_at": r["created_at"],
            })
        return items
    finally:
        conn.close()


def load_school_nanobanana_gallery(cfg: AppConfig, school_id: str, limit: int = 200) -> list:
    """같은 학교 학생들의 NanoBanana 이미지 (최신순).

    실제 이미지는 nanobanana_sessions.turns_json에 저장되어 있으므로
    sessions 테이블에서 turns를 파싱하여 이미지를 추출한다.
    """
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT ns.user_id, ns.model, ns.turns_json, ns.updated_at
            FROM nanobanana_sessions ns
            JOIN users u ON ns.user_id = u.user_id
            WHERE u.school_id = ? AND ns.turns_json IS NOT NULL
                  AND ns.turns_json != '[]'
            ORDER BY ns.updated_at DESC
            LIMIT ?
        """, (school_id, limit))
        rows = cur.fetchall()
        items = []
        for r in rows:
            turns = json.loads(r["turns_json"]) if r["turns_json"] else []
            for turn in turns:
                urls = turn.get("image_urls") or []
                if not urls:
                    continue
                items.append({
                    "user_id": r["user_id"],
                    "prompt": turn.get("prompt", ""),
                    "model_label": turn.get("model_label", r["model"] or ""),
                    "aspect_ratio": turn.get("aspect_ratio", "1:1"),
                    "image_urls": urls,
                    "created_at": r["updated_at"],
                })
        # 최신순 정렬 후 limit 적용
        items.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return items[:limit]
    finally:
        conn.close()


# ── 크레딧 관리 (통합 잔액) ─────────────────────────────


def get_user_balance(cfg: AppConfig, user_id: str) -> int:
    """통합 크레딧 잔액. 미등록이면 0."""
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        cur.execute("SELECT balance FROM user_balance WHERE user_id = ?", (user_id,))
        row = cur.fetchone()
        return int(row["balance"]) if row else 0
    finally:
        conn.close()


def set_user_balance(cfg: AppConfig, user_id: str, balance: int):
    """UPSERT: 통합 잔액 설정."""
    conn = get_db(cfg)
    try:
        conn.execute("""
            INSERT INTO user_balance (user_id, balance, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                balance = excluded.balance,
                updated_at = excluded.updated_at
        """, (user_id, int(balance), now_iso()))
        conn.commit()
    finally:
        conn.close()


def deduct_user_balance(cfg: AppConfig, user_id: str, cost: int,
                        tab_id: str = "", school_id: str = "") -> bool:
    """통합 잔액에서 cost 차감. 성공 True, 잔액 부족 False. usage_log에 tab_id 기록."""
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        cur.execute("SELECT balance FROM user_balance WHERE user_id = ?", (user_id,))
        row = cur.fetchone()
        current = int(row["balance"]) if row else 0
        if current < cost:
            return False
        ts = now_iso()
        conn.execute(
            "UPDATE user_balance SET balance = ?, updated_at = ? WHERE user_id = ?",
            (current - cost, ts, user_id),
        )
        if school_id and tab_id:
            conn.execute(
                "INSERT INTO credit_usage_log (user_id, school_id, tab_id, amount, created_at) VALUES (?, ?, ?, ?, ?)",
                (user_id, school_id, tab_id, cost, ts),
            )
        conn.commit()
        return True
    finally:
        conn.close()


def init_user_balance_from_default(cfg: AppConfig, user_id: str):
    """credit_default 설정값으로 초기 잔액 INSERT OR IGNORE."""
    conn = get_db(cfg)
    try:
        default_val = get_admin_setting(cfg, "credit_default", "0")
        balance = int(default_val) if default_val.isdigit() else 0
        conn.execute("""
            INSERT OR IGNORE INTO user_balance (user_id, balance, updated_at)
            VALUES (?, ?, ?)
        """, (user_id, balance, now_iso()))
        conn.commit()
    finally:
        conn.close()


def add_balance_bulk(
    cfg: AppConfig,
    role_filter: str,
    school_filter: str,
    amount: int,
) -> int:
    """대상 사용자들의 통합 잔액에 amount 가산. 영향받은 사용자 수 반환."""
    if amount <= 0:
        return 0

    conn = get_db(cfg)
    try:
        cur = conn.cursor()

        roles = [r.strip() for r in role_filter.split(",")]
        placeholders = ",".join("?" * len(roles))
        if school_filter and school_filter != "all":
            cur.execute(
                f"SELECT user_id FROM users WHERE role IN ({placeholders}) AND school_id = ? AND is_active = 1",
                (*roles, school_filter),
            )
        else:
            cur.execute(
                f"SELECT user_id FROM users WHERE role IN ({placeholders}) AND is_active = 1",
                roles,
            )
        user_ids = [row["user_id"] for row in cur.fetchall()]
        if not user_ids:
            return 0

        ts = now_iso()
        for uid in user_ids:
            conn.execute("""
                INSERT INTO user_balance (user_id, balance, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    balance = balance + ?,
                    updated_at = ?
            """, (uid, amount, ts, amount, ts))
        conn.commit()
        return len(user_ids)
    finally:
        conn.close()


def run_auto_credit_refill(cfg: AppConfig):
    """자동 크레딧 충전. 매월 지정일에 도달하면 1회 실행.

    admin_settings:
      credit_refill_day    — "0"이면 비활성, "1"~"28"이면 매월 해당 일
      credit_refill_last   — 마지막 실행 "YYYY-MM" (같은 월 중복 방지)
      credit_refill_amount — 충전량
    """
    from datetime import datetime, timezone

    day_str = get_admin_setting(cfg, "credit_refill_day", "0")
    try:
        refill_day = int(day_str)
    except (ValueError, TypeError):
        return
    if refill_day <= 0:
        return

    now = datetime.now(timezone.utc)
    if now.day < refill_day:
        return

    current_ym = now.strftime("%Y-%m")
    last_refill = get_admin_setting(cfg, "credit_refill_last", "")
    if last_refill == current_ym:
        return

    val = get_admin_setting(cfg, "credit_refill_amount", "0")
    try:
        amount = max(0, int(val))
    except (ValueError, TypeError):
        amount = 0

    affected = add_balance_bulk(cfg, "student,teacher", "all", amount)
    if affected >= 0:
        set_admin_setting(cfg, "credit_refill_last", current_ym)


def get_school_credit_report(cfg: AppConfig, days: int = 30) -> list[dict]:
    """학교별 크레딧 사용량 + 통합 잔액 요약.

    Returns: [{school_id, remaining, used_by_tab: {tab_id: amount}, user_count}, ...]
    """
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        since = _dt_minus_days(days)

        # 1) 사용량: credit_usage_log에서 학교·탭별 합산
        cur.execute("""
            SELECT school_id, tab_id, SUM(amount) AS used, COUNT(DISTINCT user_id) AS user_count
            FROM credit_usage_log
            WHERE created_at >= ?
            GROUP BY school_id, tab_id
        """, (since,))
        usage_rows = cur.fetchall()

        # 2) 잔액: user_balance JOIN users로 학교별 합산
        cur.execute("""
            SELECT u.school_id, SUM(ub.balance) AS remaining, COUNT(DISTINCT ub.user_id) AS user_count
            FROM user_balance ub
            JOIN users u ON u.user_id = ub.user_id
            WHERE u.is_active = 1
            GROUP BY u.school_id
        """)
        balance_rows = cur.fetchall()

        data: dict[str, dict] = {}
        for r in balance_rows:
            sid = r["school_id"]
            data[sid] = {
                "school_id": sid,
                "remaining": int(r["remaining"]),
                "user_count": int(r["user_count"]),
                "used_by_tab": {},
            }
        for r in usage_rows:
            sid = r["school_id"]
            if sid not in data:
                data[sid] = {
                    "school_id": sid,
                    "remaining": 0,
                    "user_count": int(r["user_count"]),
                    "used_by_tab": {},
                }
            data[sid]["used_by_tab"][r["tab_id"]] = int(r["used"])

        return sorted(data.values(), key=lambda x: x["school_id"])
    finally:
        conn.close()


def get_student_credit_report(
    cfg: AppConfig, school_id: str | None = None, days: int = 30
) -> list[dict]:
    """학생별 크레딧 사용량 + 잔액 요약.

    Returns: [{user_id, school_id, role, remaining, used_by_tab: {tab_id: amount}}, ...]
    """
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        since = _dt_minus_days(days)

        # 1) 사용량: credit_usage_log에서 유저·탭별 합산
        if school_id:
            cur.execute("""
                SELECT user_id, tab_id, SUM(amount) AS used
                FROM credit_usage_log
                WHERE created_at >= ? AND school_id = ?
                GROUP BY user_id, tab_id
            """, (since, school_id))
        else:
            cur.execute("""
                SELECT user_id, tab_id, SUM(amount) AS used
                FROM credit_usage_log
                WHERE created_at >= ?
                GROUP BY user_id, tab_id
            """, (since,))
        usage_rows = cur.fetchall()

        # 2) 잔액 + 유저 정보
        if school_id:
            cur.execute("""
                SELECT u.user_id, u.school_id, u.role,
                       COALESCE(ub.balance, 0) AS remaining
                FROM users u
                LEFT JOIN user_balance ub ON u.user_id = ub.user_id
                WHERE u.is_active = 1 AND u.school_id = ?
                ORDER BY u.school_id, u.user_id
            """, (school_id,))
        else:
            cur.execute("""
                SELECT u.user_id, u.school_id, u.role,
                       COALESCE(ub.balance, 0) AS remaining
                FROM users u
                LEFT JOIN user_balance ub ON u.user_id = ub.user_id
                WHERE u.is_active = 1
                ORDER BY u.school_id, u.user_id
            """)
        user_rows = cur.fetchall()

        data: dict[str, dict] = {}
        for r in user_rows:
            uid = r["user_id"]
            data[uid] = {
                "user_id": uid,
                "school_id": r["school_id"],
                "role": r["role"],
                "remaining": int(r["remaining"]),
                "used_by_tab": {},
            }
        for r in usage_rows:
            uid = r["user_id"]
            if uid not in data:
                continue
            data[uid]["used_by_tab"][r["tab_id"]] = int(r["used"])

        return sorted(data.values(), key=lambda x: (x["school_id"], x["user_id"]))
    finally:
        conn.close()


def _dt_minus_days(days: int) -> str:
    """현재 시각에서 days일 전 ISO 문자열."""
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


# ── class_schedules CRUD ──────────────────────────────────────

def list_class_schedules(cfg: AppConfig, school_id: str | None = None) -> list[dict]:
    """수업 시간표 목록 조회. school_id가 None이면 전체."""
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        if school_id:
            cur.execute(
                "SELECT * FROM class_schedules WHERE school_id = ? ORDER BY day_of_week, start_hour, start_minute",
                (school_id,),
            )
        else:
            cur.execute("SELECT * FROM class_schedules ORDER BY school_id, day_of_week, start_hour, start_minute")
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def insert_class_schedule(cfg: AppConfig, schedule: dict) -> int:
    """수업 시간표 추가. 반환: row id."""
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        n = now_iso()
        cur.execute("""
            INSERT INTO class_schedules
                (school_id, day_of_week, start_hour, start_minute, end_hour, end_minute, label, color, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            schedule["school_id"],
            schedule["day_of_week"],
            schedule["start_hour"],
            schedule.get("start_minute", 0),
            schedule["end_hour"],
            schedule.get("end_minute", 0),
            schedule.get("label", ""),
            schedule.get("color", "#6366f1"),
            n, n,
        ))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def update_class_schedule(cfg: AppConfig, schedule_id: int, schedule: dict):
    """수업 시간표 수정."""
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        cur.execute("""
            UPDATE class_schedules SET
                school_id=?, day_of_week=?, start_hour=?, start_minute=?,
                end_hour=?, end_minute=?, label=?, color=?, updated_at=?
            WHERE id=?
        """, (
            schedule["school_id"],
            schedule["day_of_week"],
            schedule["start_hour"],
            schedule.get("start_minute", 0),
            schedule["end_hour"],
            schedule.get("end_minute", 0),
            schedule.get("label", ""),
            schedule.get("color", "#6366f1"),
            now_iso(),
            schedule_id,
        ))
        conn.commit()
    finally:
        conn.close()


def delete_class_schedule(cfg: AppConfig, schedule_id: int):
    """수업 시간표 삭제."""
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM class_schedules WHERE id = ?", (schedule_id,))
        conn.commit()
    finally:
        conn.close()


def get_active_class_now(cfg: AppConfig) -> dict | None:
    """현재 시각에 진행 중인 수업이 있으면 해당 스케줄 반환, 없으면 None.
    서버 시간대는 KST(UTC+9) 기준."""
    import pytz
    kst = pytz.timezone("Asia/Seoul")
    now = datetime.now(kst)
    dow = now.weekday()  # 0=Mon
    cur_minutes = now.hour * 60 + now.minute

    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM class_schedules WHERE day_of_week = ?",
            (dow,),
        )
        for r in cur.fetchall():
            start_m = r["start_hour"] * 60 + r["start_minute"]
            end_m = r["end_hour"] * 60 + r["end_minute"]
            if start_m <= cur_minutes < end_m:
                return dict(r)
        return None
    finally:
        conn.close()


# ────────────────────────────────────────────
# 알림 (notices) CRUD
# ────────────────────────────────────────────

def create_notice(cfg: AppConfig, message: str, target_school: str = None,
                  expires_at: str = None) -> int:
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        # 기존 활성 알림 모두 비활성화 (알림은 항상 1개만 유지)
        cur.execute("UPDATE notices SET is_active=0 WHERE is_active=1")
        cur.execute("""
            INSERT INTO notices (message, target_school, is_active, created_at, expires_at)
            VALUES (?, ?, 1, ?, ?)
        """, (message, target_school, now_iso(), expires_at))
        conn.commit()
        nid = cur.lastrowid
        force_sync()
        return nid
    finally:
        conn.close()


def list_notices(cfg: AppConfig, active_only: bool = True) -> list:
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        if active_only:
            cur.execute("""
                SELECT * FROM notices
                WHERE is_active = 1
                  AND (expires_at IS NULL OR expires_at > ?)
                ORDER BY created_at DESC
            """, (now_iso(),))
        else:
            cur.execute("SELECT * FROM notices ORDER BY created_at DESC")
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def deactivate_notice(cfg: AppConfig, notice_id: int):
    conn = get_db(cfg)
    try:
        conn.execute("UPDATE notices SET is_active=0 WHERE notice_id=?", (notice_id,))
        conn.commit()
        force_sync()
    finally:
        conn.close()


def get_active_notices_for_user(cfg: AppConfig, school_id: str) -> list:
    """특정 학교 학생에게 보여줄 활성 알림 목록."""
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT * FROM notices
            WHERE is_active = 1
              AND (expires_at IS NULL OR expires_at > ?)
              AND (target_school IS NULL OR target_school = '' OR target_school = '*' OR target_school = ?)
            ORDER BY created_at DESC
        """, (now_iso(), school_id))
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


# ────────────────────────────────────────────
# 서버 점검 (maintenance) CRUD
# ────────────────────────────────────────────

def schedule_maintenance(cfg: AppConfig, scheduled_at: str, message: str = None) -> int:
    """점검 예약. scheduled_at은 ISO 형식 (e.g. '2026-03-10T18:00:00Z')."""
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO maintenance_schedule (scheduled_at, status, message, created_at)
            VALUES (?, 'scheduled', ?, ?)
        """, (scheduled_at, message or "서버 점검이 예정되어 있습니다.", now_iso()))
        conn.commit()
        mid = cur.lastrowid
        force_sync()
        return mid
    finally:
        conn.close()


def get_upcoming_maintenance(cfg: AppConfig):
    """가장 가까운 예정/진행 중 점검 조회. None이면 예정 없음."""
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT * FROM maintenance_schedule
            WHERE status IN ('scheduled', 'active')
            ORDER BY scheduled_at ASC
            LIMIT 1
        """)
        r = cur.fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


def update_maintenance_status(cfg: AppConfig, mid: int, status: str):
    conn = get_db(cfg)
    try:
        conn.execute("UPDATE maintenance_schedule SET status=? WHERE id=?", (status, mid))
        conn.commit()
        force_sync()
    finally:
        conn.close()


def cancel_maintenance(cfg: AppConfig, mid: int):
    update_maintenance_status(cfg, mid, "cancelled")


def deactivate_all_non_admin_users(cfg: AppConfig):
    """admin을 제외한 모든 사용자를 비활성화."""
    conn = get_db(cfg)
    try:
        conn.execute("""
            UPDATE users SET is_active = 0
            WHERE role != 'admin'
        """)
        conn.commit()
        force_sync()
    finally:
        conn.close()


def reactivate_all_users(cfg: AppConfig):
    """모든 사용자 재활성화."""
    conn = get_db(cfg)
    try:
        conn.execute("UPDATE users SET is_active = 1")
        conn.commit()
        force_sync()
    finally:
        conn.close()


