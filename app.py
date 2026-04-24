# app.py
import logging
import streamlit as st
import streamlit.components.v1 as components

_log = logging.getLogger(__name__)

from core.config import load_config, ensure_session_ids
from core.db import init_db, cleanup_orphan_active_jobs, ensure_notice_tables
from core.key_pool import bootstrap as key_pool_bootstrap
from ui.auth_page import render_auth_gate
from ui.admin_page import render_admin_page, render_viewer_page
from ui.sidebar import render_profile_card, render_sidebar
from ui.registry import get_all_tabs, filter_tabs
from ui.floating_chat import render_floating_chat
from ui.floating_materials import render_floating_materials
from core.schedule import check_access
from core.maintenance import check_maintenance


def main():
    cfg = load_config()

    # 기본값 세팅
    if "school_id" not in st.session_state:
        st.session_state.school_id = "default"
    ensure_session_ids()

    school_id = st.session_state.get("school_id", "default")

    from PIL import Image, ImageDraw
    _src = Image.open("Sources/aimz_browser_logo.png").convert("RGBA")
    # 파비콘용: 흰 배경 + 둥근 모서리 (다크모드 대응)
    _bbox = _src.getbbox()
    if _bbox:
        _src = _src.crop(_bbox)
    _pad = max(_src.width, _src.height) // 8
    _size = max(_src.width, _src.height) + _pad * 2
    _radius = _size // 4
    # 둥근 마스크 생성
    _mask = Image.new("L", (_size, _size), 0)
    ImageDraw.Draw(_mask).rounded_rectangle(
        [(0, 0), (_size - 1, _size - 1)], radius=_radius, fill=255,
    )
    _favicon = Image.new("RGBA", (_size, _size), (0, 0, 0, 0))
    _bg = Image.new("RGBA", (_size, _size), (255, 255, 255, 255))
    _favicon.paste(_bg, mask=_mask)
    _ox = (_size - _src.width) // 2
    _oy = (_size - _src.height) // 2
    _favicon.paste(_src, (_ox, _oy), _src)
    st.set_page_config(
        page_title="AIMZ Studio", layout="wide",
        page_icon=_favicon,
    )

    # AI 생성 대기 중 → 전체 화면 로딩 오버레이 (사이드바 조작 방지)
    _pending_keys = [
        "_gpt_pending_send",
        "_mj_pending_submit", "_mj_pending_describe",
        "_mjf_pending_submit", "_mjf_pending_describe",
        "_mjp_pending_submit", "_mjp_pending_describe",
        "_klingapi_pending_generate", "_kling_pending_generate",
        "_grok_pending_generate", "_ltx_pending_generate", "_el_pending_generate",
        "_nb_pending_generate", "_nb2_pending_generate", "_nbp_pending_generate",
    ]
    _is_pending = any(st.session_state.get(k) for k in _pending_keys)

    # 상단 헤더: Deploy·메뉴 숨김 + 로딩 오버레이 CSS (항상 포함, display만 토글)
    st.markdown(
        "<style>"
        "[data-testid='stToolbar'] .stDeployButton{display:none!important;}"
        "[data-testid='stToolbar'] [data-testid='stToolbarActions']{display:none!important;}"
        "#MainMenu{display:none!important;}"
        "footer{display:none!important;}"
        ".aimz-loading-overlay{position:fixed;inset:0;z-index:999999;background:rgba(0,0,0,0.7);"
        "display:" + ("flex" if _is_pending else "none") + ";align-items:center;justify-content:center;flex-direction:column;gap:16px;}"
        ".aimz-loading-spinner{width:48px;height:48px;border:4px solid rgba(255,255,255,0.2);"
        "border-top-color:#a78bfa;border-radius:50%;animation:aimz-spin 0.8s linear infinite;}"
        "@keyframes aimz-spin{to{transform:rotate(360deg)}}"
        ".aimz-loading-text{color:#e0e0e0;font-size:16px;font-weight:500;}"
        "</style>"
        "<div class='aimz-loading-overlay'>"
        "<div class='aimz-loading-spinner'></div>"
        "<div class='aimz-loading-text'>AI 생성 중입니다. 잠시만 기다려주세요...</div>"
        "</div>",
        unsafe_allow_html=True,
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
            _log.warning("auto_purge 실패", exc_info=True)
        st.session_state["_did_auto_purge"] = True

    # 자동 크레딧 충전 (세션당 1회)
    if "_did_credit_refill" not in st.session_state:
        try:
            from core.db import run_auto_credit_refill
            run_auto_credit_refill(cfg)
        except Exception:
            _log.warning("auto_credit_refill 실패", exc_info=True)
        st.session_state["_did_credit_refill"] = True

    # --- Auth Gate ---
    auth_user = render_auth_gate(cfg)
    if not auth_user:
        # 로그인/부트스트랩 UI가 렌더링된 상태
        # 이전 세션의 플로팅 요소 정리 (채팅, 강의자료)
        components.html("""<script>
        (function(){
          var pd=window.parent.document;
          ['fc-root','fc-styles','fm-root','fm-styles','fn-root','fn-styles'].forEach(function(id){
            var el=pd.getElementById(id); if(el) el.remove();
          });
        })();
        </script>""", height=0)
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

    # ── 알림/점검 테이블 보장 (핫 업데이트 대응) ──
    ensure_notice_tables(cfg)

    # ── 서버 점검 체크 ──
    maint_status = check_maintenance(cfg)

    # 역할별 라우팅
    if auth_user.role == "admin":
        render_profile_card(cfg)
        render_admin_page(cfg)
        return
    elif auth_user.role == "viewer":
        render_profile_card(cfg)
        render_viewer_page(cfg)
        return

    # 점검 중이면 비admin 사용자 차단
    if maint_status.is_maintenance_active:
        st.error(f"🔧 **서버 점검 중입니다.** {maint_status.message}")
        st.info("점검이 완료되면 다시 이용하실 수 있습니다.")
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

    # ── 수업 시간표 기반 접근 제어 ──
    access = check_access(cfg, school_id, auth_user.role)
    if not access.has_full_access:
        # 다른 학교 수업 중 → 갤러리 탭만 허용
        visible_tabs = [t for t in visible_tabs if t.tab_id == "gallery"]
        if not visible_tabs:
            # 갤러리 탭이 필터링으로 제외된 경우 직접 추가
            from ui.tabs.gallery_tab import TAB as GALLERY_TAB
            from ui.registry import TabSpec
            visible_tabs = [TabSpec(
                tab_id=GALLERY_TAB["tab_id"],
                title=GALLERY_TAB["title"],
                required_features=set(),
                render=GALLERY_TAB["render"],
            )]

    # 1) 프로필 카드 (최상단, 플로팅 알림 포함)
    render_profile_card(cfg)

    # 2) 페이지 타이틀 + 탭 선택
    locked_indices = {i for i, t in enumerate(visible_tabs) if t.locked}

    with st.sidebar:
        st.markdown(f"### {cfg.get_page_title(school_id)}")
        selected_idx = st.radio(
            "페이지 선택",
            options=range(len(visible_tabs)),
            format_func=lambda i: visible_tabs[i].title,
            key="selected_tab",
            label_visibility="collapsed",
        )

        # 잠긴 탭 선택 시 이전 탭으로 되돌리기
        if selected_idx in locked_indices:
            prev = st.session_state.get("_prev_tab", 0)
            st.session_state["selected_tab"] = prev
            selected_idx = prev
            st.rerun()
        # 탭 전환 시 모든 갤러리 닫기
        if selected_idx != st.session_state.get("_prev_tab"):
            for gk in ("_mj_gallery_open", "_el_gallery_open", "_klingapi_gallery_open",
                        "_kling_gallery_open", "_grok_gallery_open",
                        "_nb_gallery_open", "_nb2_gallery_open", "_nbp_gallery_open"):
                st.session_state.pop(gk, None)
        st.session_state["_prev_tab"] = selected_idx

        # 잠긴 탭 CSS (클릭 차단 + 라디오 버튼 숨김)
        if locked_indices:
            css_rules = []
            for li in locked_indices:
                nth = li + 1
                css_rules.append(
                    f'div[data-testid="stRadio"] label:nth-of-type({nth}){{'
                    f'  pointer-events:none; opacity:0.35; cursor:default;'
                    f'}}'
                    f'div[data-testid="stRadio"] label:nth-of-type({nth}) div[data-testid="stMarkdownContainer"]{{'
                    f'  font-style:italic;'
                    f'}}'
                )
            st.markdown(
                f"<style>{''.join(css_rules)}</style>",
                unsafe_allow_html=True,
            )

    # 3) 나머지 사이드바 (크레딧, 테스트모드)
    sidebar_state = render_sidebar(cfg)

    # 수업 시간 제한 안내 배너
    if not access.has_full_access:
        end_str = f"{access.active_end_hour:02d}:{access.active_end_minute:02d}"
        st.warning(
            f"현재 다른 학교의 수업 시간입니다. "
            f"**{end_str}**까지 갤러리 탭만 이용 가능합니다.",
            icon="🕐",
        )

    # 플로팅 채팅/강의자료 (teacher/student — spacer/MOCK 뒤에 배치)
    # position:fixed로 parent.document.body에 주입되므로 DOM 위치 무관.
    if auth_user.role in ("teacher", "student"):
        with st.sidebar:
            render_floating_chat(cfg)
            render_floating_materials(cfg)

    # 메인 영역: 선택된 탭 콘텐츠만 렌더링
    visible_tabs[selected_idx].render(cfg, sidebar_state)


if __name__ == "__main__":
    main()
