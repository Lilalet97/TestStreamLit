# app.py
import json
import os

import streamlit as st
import streamlit.components.v1 as components

from core.config import load_config, ensure_session_ids
from core.db import init_db, cleanup_orphan_active_jobs
from core.key_pool import bootstrap as key_pool_bootstrap
from ui.auth_page import render_auth_gate
from ui.admin_page import render_admin_page
from ui.sidebar import render_sidebar
from ui.run_detail import maybe_open_run_detail_dialog
from ui.registry import get_all_tabs, filter_tabs


def main():
    import sys
    print(f"[BOOT] Python {sys.version}", flush=True)
    print(f"[BOOT] load_config ...", flush=True)
    cfg = load_config()
    print(f"[BOOT] turso_url={cfg.turso_database_url!r:.40}", flush=True)

    # 기본값 세팅
    if "school_id" not in st.session_state:
        st.session_state.school_id = "default"
    ensure_session_ids()

    school_id = st.session_state.get("school_id", "default")

    st.set_page_config(
        page_title=cfg.get_browser_tab_title(school_id), layout="wide"
    )

    # DB 및 키풀 초기화
    print("[BOOT] init_db ...", flush=True)
    init_db(cfg)
    print("[BOOT] key_pool_bootstrap ...", flush=True)
    key_pool_bootstrap(cfg)
    print("[BOOT] bootstrap done", flush=True)

    # stale active_jobs 정리(앱 실행당 1회)
    if "_did_cleanup_active_jobs" not in st.session_state:
        cleanup_orphan_active_jobs(cfg)
        st.session_state["_did_cleanup_active_jobs"] = True

    # --- Auth Gate ---
    auth_user = render_auth_gate(cfg)
    if not auth_user:
        # 로그인/부트스트랩 UI가 렌더링된 상태
        return

    # 인증 완료 후 실제 school_id로 갱신
    # 주의: 여기서 st.rerun()을 호출하면 login_user가 큐잉한 CookieController의
    # set 명령이 브라우저에 렌더링되지 않아 쿠키가 저장되지 않음.
    prev_school_id = school_id
    school_id = auth_user.school_id

    # set_page_config은 이미 호출되었으므로, 탭 제목이 달라졌으면 JS로 동적 갱신
    # st.markdown은 <script>를 제거하므로 components.html을 사용 (iframe → parent 접근)
    if school_id != prev_school_id:
        actual_title = cfg.get_browser_tab_title(school_id)
        components.html(
            f"<script>parent.document.title = {actual_title!r};</script>",
            height=0,
        )

    # 운영 계정이면 운영 페이지로 라우팅
    if auth_user.role == "admin":
        render_admin_page(cfg)
        return

    # --- User UI ---
    sidebar_state = render_sidebar(cfg)

    # 키 풀 상태를 사이드바 하단에 간결하게 표시
    raw = os.getenv("KEY_POOL_JSON") or st.secrets.get("KEY_POOL_JSON", "")
    with st.sidebar:
        st.markdown("---")
        if raw:
            try:
                kp = json.loads(raw)
                providers = "  ".join(f"`{k}` **{len(v)}**" for k, v in kp.items())
                st.markdown(f"🔑 키 풀 &nbsp; {providers}")
            except Exception:
                st.warning("키 풀 JSON 파싱 실패")
        else:
            st.caption("🔑 키 풀 미설정")

    st.title(cfg.get_page_title(school_id))

    maybe_open_run_detail_dialog(cfg)

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
