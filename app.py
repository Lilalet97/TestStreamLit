# app.py
import streamlit as st
import streamlit.components.v1 as components

from core.config import load_config, ensure_session_ids
from core.db import init_db, cleanup_orphan_active_jobs
from core.key_pool import bootstrap as key_pool_bootstrap
from ui.auth_page import render_auth_gate
from ui.admin_page import render_admin_page, render_viewer_page
from ui.sidebar import render_profile_card, render_sidebar
from ui.registry import get_all_tabs, filter_tabs
from ui.floating_chat import render_floating_chat


def main():
    cfg = load_config()

    # 기본값 세팅
    if "school_id" not in st.session_state:
        st.session_state.school_id = "default"
    ensure_session_ids()

    school_id = st.session_state.get("school_id", "default")

    st.set_page_config(
        page_title=cfg.get_browser_tab_title(school_id), layout="wide"
    )

    # DB 및 키풀 초기화 (프로세스당 1회만 실행)
    init_db(cfg)
    key_pool_bootstrap(cfg)

    # stale active_jobs 정리(앱 실행당 1회)
    if "_did_cleanup_active_jobs" not in st.session_state:
        cleanup_orphan_active_jobs(cfg)
        st.session_state["_did_cleanup_active_jobs"] = True

    # 자동 삭제 (세션당 1회)
    if "_did_auto_purge" not in st.session_state:
        try:
            from core.db import run_auto_purge
            run_auto_purge(cfg)
        except Exception:
            pass
        st.session_state["_did_auto_purge"] = True

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
    # sidebar에 렌더링: GPT 탭 CSS(.stMainBlockContainer iframe)가 이 iframe을
    # 전체화면으로 확장하여 탭 콘텐츠를 가리는 문제 방지
    if school_id != prev_school_id:
        actual_title = cfg.get_browser_tab_title(school_id)
        with st.sidebar:
            components.html(
                f"<script>parent.document.title = {actual_title!r};</script>",
                height=0,
            )

    # 역할별 라우팅
    if auth_user.role == "admin":
        render_profile_card(cfg)
        with st.sidebar:
            st.markdown("### 🛠️ 운영 페이지")
        render_admin_page(cfg)
        return
    elif auth_user.role == "viewer":
        render_profile_card(cfg)
        with st.sidebar:
            st.markdown("### 👁️ 모니터링 페이지")
        render_viewer_page(cfg)
        return

    # --- User UI (teacher / student) ---

    # 탭 목록 준비 (사이드바에서 선택 UI를 먼저 렌더링하기 위해 선행 계산)
    enabled_features = set(cfg.get_enabled_features(school_id))
    all_tabs = get_all_tabs()
    visible_tabs = filter_tabs(all_tabs, enabled_features)

    if not visible_tabs:
        st.warning(
            f"이 학교({school_id})는 현재 오픈된 탭이 없습니다.\n"
            f"- enabled_features: {sorted(enabled_features)}"
        )
        return

    # 1) 프로필 카드 (최상단)
    render_profile_card(cfg)

    # 2) 페이지 타이틀 + 탭 선택
    with st.sidebar:
        st.markdown(f"### {cfg.get_page_title(school_id)}")
        selected_idx = st.radio(
            "페이지 선택",
            options=range(len(visible_tabs)),
            format_func=lambda i: visible_tabs[i].title,
            key="selected_tab",
            label_visibility="collapsed",
        )

    # 3) 나머지 사이드바 (동시실행, 테스트모드)
    sidebar_state = render_sidebar(cfg)

    # 메인 영역: 선택된 탭 콘텐츠만 렌더링
    visible_tabs[selected_idx].render(cfg, sidebar_state)

    # 플로팅 채팅 (teacher/student만)
    # sidebar에 렌더링: 채팅 iframe(1px)이 .stMainBlockContainer에 있으면
    # GPT 탭 CSS가 전체화면으로 확장하여 빈 공간 생성. sidebar는 CSS 영향 밖.
    # 채팅 UI는 parent.document.body에 position:fixed로 주입되므로 위치 무관.
    if auth_user.role in ("teacher", "student"):
        with st.sidebar:
            render_floating_chat(cfg)


if __name__ == "__main__":
    main()
