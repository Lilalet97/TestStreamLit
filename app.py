# app.py
import json
import os

import streamlit as st

from core.config import load_config, ensure_session_ids
from core.db import init_db, cleanup_orphan_active_jobs
from core.key_pool import bootstrap as key_pool_bootstrap
from ui.auth_page import render_auth_gate
from ui.admin_page import render_admin_page
from ui.sidebar import render_sidebar
from ui.run_detail import maybe_open_run_detail_dialog
from ui.registry import get_all_tabs, filter_tabs


def main():
    st.set_page_config(page_title="Generative AI Multi-API Full Tester", layout="wide")

    cfg = load_config()

    # 기본값 세팅
    if "school_id" not in st.session_state:
        st.session_state.school_id = "default"
    ensure_session_ids()

    # DB 및 키풀 초기화
    init_db(cfg)
    key_pool_bootstrap(cfg)

    # stale active_jobs 정리(앱 실행당 1회)
    if "_did_cleanup_active_jobs" not in st.session_state:
        cleanup_orphan_active_jobs(cfg)
        st.session_state["_did_cleanup_active_jobs"] = True

    # --- Auth Gate ---
    auth_user = render_auth_gate(cfg)
    if not auth_user:
        # 로그인/부트스트랩 UI가 렌더링된 상태
        return

    # 운영 계정이면 운영 페이지로 라우팅
    if auth_user.role == "admin":
        render_admin_page(cfg)
        return

    # --- User UI ---
    sidebar_state = render_sidebar(cfg)

    # (선택) 현재 KEY_POOL_JSON 로드 여부만 사이드바에 표시
    raw = os.getenv("KEY_POOL_JSON") or st.secrets.get("KEY_POOL_JSON", "")
    st.sidebar.write("KEY_POOL_JSON loaded:", bool(raw))
    if raw:
        try:
            kp = json.loads(raw)
            st.sidebar.write({k: len(v) for k, v in kp.items()})
        except Exception:
            st.sidebar.write("KEY_POOL_JSON parse failed")

    st.title("🚀 Generative AI Multi-API Full Tester")

    maybe_open_run_detail_dialog(cfg)

    school_id = st.session_state.get("school_id", "default")

    # enabled_features는 tenant json(default.json/school_a.json)을 우선 사용
    enabled_features = set(cfg.get_enabled_features(school_id))

    all_tabs = get_all_tabs()
    visible_tabs = filter_tabs(all_tabs, enabled_features)

    if not visible_tabs:
        st.warning(
            f"이 학교({school_id})는 현재 오픈된 탭이 없습니다.\n"
            f"- enabled_features: {sorted(enabled_features)}"
        )
        return

    tab_objs = st.tabs([t.title for t in visible_tabs])
    for tab_obj, tab_def in zip(tab_objs, visible_tabs):
        with tab_obj:
            tab_def.render(cfg, sidebar_state)


if __name__ == "__main__":
    main()
