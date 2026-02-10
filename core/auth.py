# core/auth.py
"""Authentication helpers.

- Stores password hashes in SQLite (table: users)
- Hash format: pbkdf2_sha256$<iterations>$<salt_b64>$<hash_b64>

This is intentionally dependency-free (stdlib only).
"""

import base64
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

COOKIE_NAME = "auth_token"
COOKIE_CTRL_KEY = "auth_cookie_controller_v1"
DEFAULT_SESSION_TTL_SEC = 24 * 60 * 60  # 24h

def _cookies() -> CookieController:
    # 매 run마다 새로 생성해야 컴포넌트가 렌더링되어 브라우저 쿠키를 읽을 수 있음.
    # session_state에 캐시하면 F5 이후 컴포넌트가 재렌더링되지 않아 쿠키 복원 불가.
    return CookieController(key=COOKIE_CTRL_KEY)

def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def _b64d(s: str) -> bytes:
    pad = "=" * ((4 - (len(s) % 4)) % 4)
    return base64.urlsafe_b64decode((s + pad).encode("ascii"))


def hash_password(password: str, iterations: int = 200_000) -> str:
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
    ph = hash_password(admin_pass)
    upsert_user(cfg, user_id=admin_user, password_hash=ph, role="admin", school_id=admin_school, is_active=1)


def authenticate(cfg: AppConfig, user_id: str, password: str) -> Optional[AuthUser]:
    row = get_user(cfg, user_id)
    if not row:
        return None
    if int(row["is_active"]) != 1:
        return None
    if not verify_password(password, row["password_hash"]):
        return None
    return AuthUser(user_id=row["user_id"], role=row["role"], school_id=row["school_id"])

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
            ctrl.remove(COOKIE_NAME)
            _auth_dbg(cfg, "cookie removed (no db session)")
        except Exception as exc:
            _auth_dbg(cfg, f"cookie remove error: {type(exc).__name__}: {exc}")
        return None

    urow = get_user(cfg, srow["user_id"])
    _auth_dbg(cfg, f"user row found={bool(urow)} is_active={(urow['is_active'] if urow else None)}")
    if not urow or int(urow["is_active"]) != 1:
        try:
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

    user = AuthUser(user_id=urow["user_id"], role=urow["role"], school_id=urow["school_id"])
    st.session_state["auth_session_token"] = token

    # session_state만 세팅 (쿠키는 이미 있으니 다시 set 안 함)
    login_user(cfg, user, remember=False)
    _auth_dbg(cfg, "restore success -> session_state populated")
    return user


def is_bootstrap_needed(cfg: AppConfig) -> bool:
    return not users_exist(cfg)


def login_user(cfg: AppConfig, user: AuthUser, remember: bool = True):
    st.session_state["auth_logged_in"] = True
    st.session_state["auth_user_id"] = user.user_id
    st.session_state["auth_role"] = user.role
    st.session_state["auth_school_id"] = user.school_id
    st.session_state.user_id = user.user_id
    st.session_state.school_id = user.school_id

    if remember:
        token = create_user_session(cfg, user.user_id, user.role, user.school_id, ttl_sec=DEFAULT_SESSION_TTL_SEC)
        st.session_state["auth_session_token"] = token

        try:
            ctrl = _cookies()

            # ✅ max_age 옵션 제거 (패키지 구현에 따라 실패/무시될 수 있음)
            ctrl.set(COOKIE_NAME, token)

            # ✅ 저장 직후 read-back (DEBUG_AUTH=1일 때만 콘솔로 확인)
            if os.getenv("DEBUG_AUTH", "0") == "1":
                got = str(ctrl.get(COOKIE_NAME) or "").strip()
                print(f"[AUTH-DBG] cookie set -> readback len={len(got)} head={got[:8]}")
        except Exception as e:
            if os.getenv("DEBUG_AUTH", "0") == "1":
                print(f"[AUTH-DBG] cookie set FAILED: {type(e).__name__}: {e}")


def logout_user(cfg: AppConfig):
    token = st.session_state.get("auth_session_token") or ""
    if token:
        try:
            revoke_user_session(cfg, token)
        except Exception:
            pass
        try:
            _cookies().remove(COOKIE_NAME)
        except Exception:
            pass

    for k in ["auth_logged_in", "auth_user_id", "auth_role", "auth_school_id", "auth_session_token"]:
        st.session_state.pop(k, None)

    # 세션 ID 갱신 → 이전 유저의 세션 기록과 분리
    st.session_state.session_id = str(uuid.uuid4())
    st.session_state.user_id = "guest"
    st.session_state.school_id = "default"

    # result_store 세션 내 히스토리 정리 (이전 유저 결과 제거)
    for prefix in ("kling", "legnext"):
        for suffix in ("history", "last", "inflight"):
            st.session_state.pop(f"_rs:{prefix}:{suffix}", None)


def current_user() -> Optional[AuthUser]:
    if not st.session_state.get("auth_logged_in"):
        return None
    uid = st.session_state.get("auth_user_id") or ""
    role = st.session_state.get("auth_role") or "user"
    sid = st.session_state.get("auth_school_id") or st.session_state.get("school_id") or "default"
    if not uid:
        return None
    return AuthUser(user_id=uid, role=role, school_id=sid)

def _auth_dbg(cfg, msg: str):
    if (os.getenv("DEBUG_AUTH", "0") or "").strip() == "1":
        print(f"[AUTH-DBG] {msg}")