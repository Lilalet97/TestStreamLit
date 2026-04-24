# core/auth.py
"""Authentication helpers.

- Stores password hashes in SQLite (table: users)
- Hash format: pbkdf2_sha256$<iterations>$<salt_b64>$<hash_b64>

This is intentionally dependency-free (stdlib only).
"""

import base64
import logging
import hashlib
import hmac
import os
import uuid
from dataclasses import dataclass
from typing import Optional

import streamlit as st

from core.config import AppConfig
from core.db import (
    users_exist, get_user, upsert_user,
    create_user_session, get_user_session, touch_user_session, revoke_user_session
)
from streamlit_cookies_controller import CookieController


@dataclass(frozen=True)
class AuthUser:
    user_id: str
    role: str
    school_id: str
    nickname: str = ""

VALID_ROLES = ("admin", "viewer", "teacher", "student")

COOKIE_NAME = "auth_token"
COOKIE_CTRL_KEY = "auth_cookie_controller_v1"
DEFAULT_SESSION_TTL_SEC = 24 * 60 * 60  # 24h

def _cookies() -> CookieController:
    # 매 run마다 새로 생성해야 컴포넌트가 렌더링되어 브라우저 쿠키를 읽을 수 있음.
    # session_state에 캐시하면 F5 이후 컴포넌트가 재렌더링되지 않아 쿠키 복원 불가.
    # sidebar에 렌더링: GPT 탭 CSS(.stMainBlockContainer iframe)가
    # CookieController iframe을 전체화면으로 확장하여 탭 콘텐츠를 가리는 문제 방지.
    with st.sidebar:
        return CookieController(key=COOKIE_CTRL_KEY)

def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def _b64d(s: str) -> bytes:
    pad = "=" * ((4 - (len(s) % 4)) % 4)
    return base64.urlsafe_b64decode((s + pad).encode("ascii"))


def hash_password(password: str, iterations: int = 600_000) -> str:
    if not isinstance(password, str) or not password:
        raise ValueError("password is required")
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${_b64e(salt)}${_b64e(dk)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, it_s, salt_s, hash_s = (stored or "").split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        iterations = int(it_s)
        salt = _b64d(salt_s)
        expected = _b64d(hash_s)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return hmac.compare_digest(dk, expected)
    except Exception:
        return False


def _get_secret_or_env(key: str, default: str = "") -> str:
    try:
        v = str(st.secrets.get(key, "") or "").strip()
    except Exception:
        v = ""
    if not v:
        v = (os.getenv(key, default) or "").strip()
    return v


def maybe_seed_admin_from_env(cfg: AppConfig):
    """Optional seeding: if ADMIN_USER/ADMIN_PASS are provided, ensure that admin exists."""
    admin_user = _get_secret_or_env("ADMIN_USER", "")
    admin_pass = _get_secret_or_env("ADMIN_PASS", "")
    admin_school = _get_secret_or_env("ADMIN_SCHOOL_ID", "default") or "default"
    if not admin_user or not admin_pass:
        return
    existing = get_user(cfg, admin_user)
    if existing:
        return  # admin already exists, don't overwrite
    ph = hash_password(admin_pass)
    upsert_user(cfg, user_id=admin_user, password_hash=ph, role="admin", school_id=admin_school, is_active=1)


# ── 로그인 시도 제한 ──
# NOTE: in-memory only — resets on process restart
_LOGIN_ATTEMPTS: dict[str, list[float]] = {}  # user_id -> [timestamp, ...]
_MAX_ATTEMPTS = 5
_LOCKOUT_SEC = 60  # 5회 실패 시 60초 잠금
import time as _time


def _check_login_rate(user_id: str) -> Optional[str]:
    """로그인 시도 횟수 확인. 잠금 중이면 에러 메시지 반환, 아니면 None."""
    now = _time.time()
    attempts = _LOGIN_ATTEMPTS.get(user_id, [])
    # 만료된 시도 제거
    attempts = [t for t in attempts if now - t < _LOCKOUT_SEC]
    _LOGIN_ATTEMPTS[user_id] = attempts
    if len(attempts) >= _MAX_ATTEMPTS:
        remaining = int(_LOCKOUT_SEC - (now - attempts[0]))
        return f"로그인 시도 횟수를 초과했습니다. {remaining}초 후 다시 시도해주세요."
    return None


def _record_login_failure(user_id: str):
    """실패 기록."""
    _LOGIN_ATTEMPTS.setdefault(user_id, []).append(_time.time())


def _clear_login_attempts(user_id: str):
    """성공 시 기록 초기화."""
    _LOGIN_ATTEMPTS.pop(user_id, None)


def authenticate(cfg: AppConfig, user_id: str, password: str) -> Optional[AuthUser]:
    # 브루트포스 방어
    lockout_msg = _check_login_rate(user_id)
    if lockout_msg:
        return None  # 잠금 상태

    row = get_user(cfg, user_id)
    if not row:
        # Timing equalization: burn CPU so "user not found" takes the same time as "wrong password"
        hash_password("dummy_timing_equalization")
        _record_login_failure(user_id)
        return None
    if int(row["is_active"]) != 1:
        # 점검 중 비활성화인지 확인
        from core.maintenance import check_maintenance
        maint = check_maintenance(cfg)
        if maint.is_maintenance_active:
            raise RuntimeError("서버 점검 중입니다. 잠시 후 다시 시도해주세요.")
        return None
    if not verify_password(password, row["password_hash"]):
        _record_login_failure(user_id)
        return None
    _clear_login_attempts(user_id)
    return AuthUser(user_id=row["user_id"], role=row["role"], school_id=row["school_id"], nickname=row.get("nickname") or "")

def try_restore_login(cfg: AppConfig) -> Optional[AuthUser]:
    if st.session_state.get("auth_logged_in"):
        _auth_dbg(cfg, "already logged in (session_state)")
        return current_user()

    ctrl = _cookies()

    try:
        token = str(ctrl.get(COOKIE_NAME) or "").strip()
    except Exception as e:
        _auth_dbg(cfg, f"cookie get error: {type(e).__name__}: {e}")
        token = ""

    _auth_dbg(cfg, f"cookie token len={len(token)} head={token[:6]}")

    # CookieController는 브라우저에서 쿠키를 읽어 자동 rerun을 트리거함.
    # 첫 렌더에서는 아직 쿠키가 도착하지 않았을 수 있으므로 None을 반환하고,
    # 컴포넌트의 자동 rerun에 의존함. (수동 st.rerun()은 partial output을 폐기하므로 제거)
    if not token:
        _auth_dbg(cfg, "no cookie token -> cannot restore")
        return None

    srow = get_user_session(cfg, token)
    _auth_dbg(cfg, f"db session found={bool(srow)}")
    if not srow:
        try:
            with st.sidebar:
                ctrl.remove(COOKIE_NAME)
            _auth_dbg(cfg, "cookie removed (no db session)")
        except Exception as exc:
            _auth_dbg(cfg, f"cookie remove error: {type(exc).__name__}: {exc}")
        return None

    urow = get_user(cfg, srow["user_id"])
    _auth_dbg(cfg, f"user row found={bool(urow)} is_active={(urow['is_active'] if urow else None)}")
    if not urow or int(urow["is_active"]) != 1:
        try:
            with st.sidebar:
                ctrl.remove(COOKIE_NAME)
            _auth_dbg(cfg, "cookie removed (user missing/inactive)")
        except Exception as exc:
            _auth_dbg(cfg, f"cookie remove error: {type(exc).__name__}: {exc}")
        return None

    try:
        touch_user_session(cfg, token)
        _auth_dbg(cfg, "touch session ok")
    except Exception as exc:
        _auth_dbg(cfg, f"touch session error: {type(exc).__name__}: {exc}")

    user = AuthUser(user_id=urow["user_id"], role=urow["role"], school_id=urow["school_id"], nickname=urow.get("nickname") or "")
    st.session_state["auth_session_token"] = token

    # session_state만 세팅 (쿠키는 이미 있으니 다시 set 안 함)
    login_user(cfg, user, remember=False)
    _auth_dbg(cfg, "restore success -> session_state populated")
    return user


def is_bootstrap_needed(cfg: AppConfig) -> bool:
    return not users_exist(cfg)


def login_user(cfg: AppConfig, user: AuthUser, remember: bool = True):
    # 게스트 → 로그인 전환 시 이전 세션 데이터 제거 (데이터 유출 방지)
    prev_uid = st.session_state.get("auth_user_id", "")
    if not prev_uid or prev_uid == "guest" or prev_uid != user.user_id:
        for k in ("mj_gallery", "_mj_db_loaded", "_mj_processed_actions", "_mj_pending_submit",
                   "gpt_conversations", "gpt_active_id", "_gpt_db_loaded", "_gpt_processed_actions",
                   "elevenlabs_history", "_elevenlabs_db_loaded", "_el_processed_actions",
                   "kling_web_history", "_kling_db_loaded", "_kling_processed_actions",
                   "klingapi_history", "_klingapi_db_loaded", "_klingapi_processed_actions",
                   "kling_grok_history", "_grok_db_loaded", "_grok_processed_actions",
                   "nb_sessions", "nb_active_id", "_nb_db_loaded"):
            st.session_state.pop(k, None)

    st.session_state["auth_logged_in"] = True
    st.session_state["auth_user_id"] = user.user_id
    st.session_state["auth_role"] = user.role
    st.session_state["auth_school_id"] = user.school_id
    st.session_state["auth_nickname"] = user.nickname
    st.session_state.user_id = user.user_id
    st.session_state.school_id = user.school_id

    if remember:
        token = create_user_session(cfg, user.user_id, user.role, user.school_id, ttl_sec=DEFAULT_SESSION_TTL_SEC)
        st.session_state["auth_session_token"] = token

        try:
            # sidebar에서 모든 CookieController 렌더링 수행
            # ctrl.set()도 내부적으로 컴포넌트를 렌더할 수 있어
            # .stMainBlockContainer에 남으면 GPT 탭 CSS가 전체화면으로 확장함
            with st.sidebar:
                ctrl = _cookies()
                ctrl.set(COOKIE_NAME, token)

        except Exception:
            pass


def logout_user(cfg: AppConfig):
    token = st.session_state.get("auth_session_token") or ""
    if token:
        try:
            revoke_user_session(cfg, token)
        except Exception:
            pass
        try:
            with st.sidebar:
                _cookies().remove(COOKIE_NAME)
        except Exception:
            pass

    for k in ["auth_logged_in", "auth_user_id", "auth_role", "auth_school_id", "auth_nickname", "auth_session_token"]:
        st.session_state.pop(k, None)

    # 세션 ID 갱신 → 이전 유저의 세션 기록과 분리
    st.session_state.session_id = str(uuid.uuid4())
    st.session_state.user_id = "guest"
    st.session_state.school_id = "default"

    # MJ 갤러리 세션 상태 정리
    for k in ("mj_gallery", "_mj_db_loaded", "_mj_processed_actions", "_mj_pending_submit"):
        st.session_state.pop(k, None)

    # GPT Chat 세션 상태 정리
    for k in ("gpt_conversations", "gpt_active_id", "_gpt_db_loaded", "_gpt_processed_actions", "_gpt_pending_send"):
        st.session_state.pop(k, None)

    # NanoBanana 세션 상태 정리
    for k in ("nb_sessions", "nb_active_id", "_nb_db_loaded", "_nb_processed_actions", "_nb_pending_generate"):
        st.session_state.pop(k, None)

    # Kling 세션 상태 정리
    for k in ("kling_web_history", "_kling_db_loaded", "_kling_processed_actions", "_kling_pending_generate"):
        st.session_state.pop(k, None)

    # KlingAPI 세션 상태 정리
    for k in ("klingapi_history", "_klingapi_db_loaded", "_klingapi_processed_actions"):
        st.session_state.pop(k, None)

    # Kling Grok 세션 상태 정리
    for k in ("kling_grok_history", "_grok_db_loaded", "_grok_processed_actions"):
        st.session_state.pop(k, None)

    # ElevenLabs 세션 상태 정리
    for k in ("elevenlabs_history", "_elevenlabs_db_loaded", "_el_processed_actions",
              "_el_pending_generate", "_el_cloned_voices", "_el_clone_result",
              "_el_error_msg", "_el_credit_toast", "_el_gallery_open"):
        st.session_state.pop(k, None)

    # 플로팅 채팅 세션 상태 정리
    st.session_state.pop("_chat_last_ts", None)


def current_user() -> Optional[AuthUser]:
    if not st.session_state.get("auth_logged_in"):
        return None
    uid = st.session_state.get("auth_user_id") or ""
    role = st.session_state.get("auth_role") or "student"
    sid = st.session_state.get("auth_school_id") or st.session_state.get("school_id") or "default"
    nick = st.session_state.get("auth_nickname") or ""
    if not uid:
        return None
    return AuthUser(user_id=uid, role=role, school_id=sid, nickname=nick)

def _auth_dbg(cfg, msg: str):
    if (os.getenv("DEBUG_AUTH", "0") or "").strip() == "1":
        logging.getLogger(__name__).debug("[AUTH-DBG] %s", msg)