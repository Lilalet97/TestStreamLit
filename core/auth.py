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
from dataclasses import dataclass
from typing import Optional

import streamlit as st

from core.config import AppConfig
from core.db import users_exist, get_user, upsert_user


@dataclass(frozen=True)
class AuthUser:
    user_id: str
    role: str
    school_id: str


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


def is_bootstrap_needed(cfg: AppConfig) -> bool:
    return not users_exist(cfg)


def login_user(user: AuthUser):
    st.session_state["auth_logged_in"] = True
    st.session_state["auth_user_id"] = user.user_id
    st.session_state["auth_role"] = user.role
    st.session_state["auth_school_id"] = user.school_id
    # keep compatibility with existing code
    st.session_state.user_id = user.user_id
    st.session_state.school_id = user.school_id


def logout_user():
    for k in ["auth_logged_in", "auth_user_id", "auth_role", "auth_school_id"]:
        st.session_state.pop(k, None)
    st.session_state.user_id = "guest"
    st.session_state.school_id = "default"


def current_user() -> Optional[AuthUser]:
    if not st.session_state.get("auth_logged_in"):
        return None
    uid = st.session_state.get("auth_user_id") or ""
    role = st.session_state.get("auth_role") or "user"
    sid = st.session_state.get("auth_school_id") or st.session_state.get("school_id") or "default"
    if not uid:
        return None
    return AuthUser(user_id=uid, role=role, school_id=sid)
