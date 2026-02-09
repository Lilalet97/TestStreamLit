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
)
from core.db import upsert_user


def render_auth_gate(cfg: AppConfig):
    """Ensure the user is authenticated.

    - If first run and no users exist -> bootstrap admin
    - Else -> login page

    Returns:
        AuthUser if logged in, else None (and renders UI)
    """
    # Optional admin seeding (for headless deployments)
    maybe_seed_admin_from_env(cfg)

    u = current_user()
    if u:
        return u

    if is_bootstrap_needed(cfg):
        return _render_bootstrap_admin(cfg)
    return _render_login(cfg)


def _render_bootstrap_admin(cfg: AppConfig):
    st.title("🔐 초기 관리자 계정 생성")
    st.info("처음 실행입니다. 운영팀(관리자) 계정을 먼저 만들어야 합니다.")

    with st.form("bootstrap_admin"):
        user_id = st.text_input("관리자 ID")
        pw1 = st.text_input("비밀번호", type="password")
        pw2 = st.text_input("비밀번호 확인", type="password")
        school_id = st.text_input("기본 School ID", value="default")
        submitted = st.form_submit_button("관리자 생성")

    if not submitted:
        return None

    user_id = (user_id or "").strip()
    school_id = (school_id or "default").strip() or "default"

    if not user_id:
        st.error("관리자 ID를 입력하세요.")
        return None
    if not pw1:
        st.error("비밀번호를 입력하세요.")
        return None
    if pw1 != pw2:
        st.error("비밀번호가 일치하지 않습니다.")
        return None

    ph = hash_password(pw1)
    upsert_user(cfg, user_id=user_id, password_hash=ph, role="admin", school_id=school_id, is_active=1)
    login_user(AuthUser(user_id=user_id, role="admin", school_id=school_id))
    st.success("관리자 계정이 생성되었습니다. 로그인 완료.")
    st.rerun()


def _render_login(cfg: AppConfig):
    st.title("로그인")

    with st.form("login_form"):
        user_id = st.text_input("ID")
        password = st.text_input("비밀번호", type="password")
        submitted = st.form_submit_button("로그인")

    if not submitted:
        return None

    user_id = (user_id or "").strip()
    if not user_id or not password:
        st.error("ID/비밀번호를 입력하세요.")
        return None

    u = authenticate(cfg, user_id=user_id, password=password)
    if not u:
        st.error("로그인 실패: ID/비밀번호를 확인하세요(또는 계정이 비활성화 상태일 수 있습니다).")
        return None

    login_user(u)
    st.success("로그인 성공")
    st.rerun()
