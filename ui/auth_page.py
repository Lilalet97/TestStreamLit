# ui/auth_page.py
import streamlit as st

from core.config import AppConfig
from core.auth import (
    AuthUser,
    authenticate,
    current_user,
    is_bootstrap_needed,
    login_user,
    maybe_seed_admin_from_env,
    hash_password,
    try_restore_login,
)
from core.db import upsert_user

# ✅ 로그인 확정 요청을 임시로 저장할 session_state 키
_PENDING_LOGIN_KEY = "_auth_pending_login"


def render_auth_gate(cfg: AppConfig):
    """Ensure the user is authenticated.

    Returns:
        AuthUser if logged in, else None (and renders UI)
    """
    maybe_seed_admin_from_env(cfg)

    # ------------------------------------------------------------
    # ✅ (1) 폼에서 "로그인 확정 요청"만 해두고 rerun된 케이스 처리
    #     -> 여기(placeholder 밖)에서 login_user를 실행해야 쿠키가 안정적으로 남습니다.
    # ------------------------------------------------------------
    pending = st.session_state.pop(_PENDING_LOGIN_KEY, None)
    if isinstance(pending, dict):
        user = AuthUser(
            user_id=pending.get("user_id", ""),
            role=pending.get("role", "user"),
            school_id=pending.get("school_id", "default"),
        )
        if user.user_id:
            login_user(cfg, user, remember=True)
            if getattr(cfg, "debug_auth", False):
                token = st.session_state.get("auth_session_token", "")
                st.sidebar.success(f"[AUTH-DBG] login_user done, token head={str(token)[:6]}")
            return current_user()

    # ------------------------------------------------------------
    # ✅ (2) 쿠키/DB 세션 복구 시도 (F5 대응)
    # ------------------------------------------------------------
    restored = try_restore_login(cfg)
    if restored:
        return restored

    u = current_user()
    if u:
        return u

    # ------------------------------------------------------------
    # (3) 쿠키 hydration 대기
    #     CookieController는 첫 렌더 시 브라우저 쿠키를 비동기로 읽음.
    #     첫 run에서는 쿠키가 아직 없을 수 있으므로 로딩 상태를 표시하고,
    #     컴포넌트의 자동 rerun을 기다림.
    # ------------------------------------------------------------
    if not st.session_state.get("_auth_cookies_checked"):
        st.session_state["_auth_cookies_checked"] = True
        st.info("로그인 확인 중...")
        return None

    # ------------------------------------------------------------
    # (4) 아직 로그인 안 됐으면 bootstrap/login 화면
    # ------------------------------------------------------------
    if is_bootstrap_needed(cfg):
        return _render_bootstrap_admin(cfg)
    return _render_login(cfg)


def _render_bootstrap_admin(cfg: AppConfig):
    st.title("초기 관리자 생성")
    st.write("첫 실행입니다. 관리자 계정을 생성해주세요.")

    with st.form("bootstrap_admin_form"):
        user_id = st.text_input("관리자 ID", value="admin")
        password = st.text_input("관리자 PW", type="password")
        school_id = st.text_input("학교 ID", value="default")
        submitted = st.form_submit_button("생성", use_container_width=True)

    if submitted:
        user_id = (user_id or "").strip()
        school_id = (school_id or "default").strip() or "default"

        if not user_id or not password:
            st.error("ID/PW를 입력해주세요.")
            return None

        upsert_user(
            cfg,
            user_id=user_id,
            password_hash=hash_password(password),
            role="admin",
            school_id=school_id,
            is_active=1,
        )

        # ✅ 여기서 login_user 호출하지 말고, auth gate 최상단에서 처리하도록 넘김
        st.session_state[_PENDING_LOGIN_KEY] = {
            "user_id": user_id,
            "role": "admin",
            "school_id": school_id,
        }
        st.rerun()

    return None


def _render_login(cfg: AppConfig):
    st.title("로그인")

    with st.form("login_form"):
        user_id = st.text_input("ID")
        password = st.text_input("PW", type="password")
        submitted = st.form_submit_button("로그인", use_container_width=True)

    if submitted:
        user_id = (user_id or "").strip()
        if not user_id or not password:
            st.error("ID/PW를 입력해주세요.")
            return None

        u = authenticate(cfg, user_id, password)
        if not u:
            st.error("로그인 실패: ID/PW를 확인해주세요.")
            return None

        # ✅ 여기서 login_user 호출하지 말고, auth gate 최상단에서 처리하도록 넘김
        st.session_state[_PENDING_LOGIN_KEY] = {
            "user_id": u.user_id,
            "role": u.role,
            "school_id": u.school_id,
        }
        st.rerun()

    return None
