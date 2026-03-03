# ui/sidebar.py
import base64
import streamlit as st
import streamlit.components.v1 as components
from dataclasses import dataclass

from pathlib import Path

from core.config import AppConfig
from core.auth import current_user, logout_user

_LOGO_DIR = Path(__file__).resolve().parent.parent / "Sources"


@dataclass
class SidebarState:
    test_mode: bool


def _encode_logo(path: str) -> str:
    """로고 이미지를 base64로 인코딩 (HTML 인라인 사용)."""
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def _role_badge(role: str) -> str:
    colors = {"admin": "#e74c3c", "viewer": "#e67e22", "teacher": "#2ecc71", "student": "#3498db"}
    bg = colors.get(role, "#95a5a6")
    return (
        f'<span style="background:{bg};color:#fff;padding:2px 8px;'
        f'border-radius:10px;font-size:0.75em;font-weight:600;'
        f'letter-spacing:0.5px;">{role.upper()}</span>'
    )


def render_profile_card(cfg: AppConfig) -> None:
    """사이드바 최상단 프로필 카드 + 로그아웃 버튼."""
    u = current_user()

    with st.sidebar:
        # ── 회사 로고 ──
        _logo_file = _LOGO_DIR / "aimz_CI_logo_aimz_signature_white.png"
        if _logo_file.exists():
            _b64 = _encode_logo(str(_logo_file))
            st.markdown(
                f'<div style="padding:0 4px;margin-bottom:4px;pointer-events:none;">'
                f'<img src="data:image/png;base64,{_b64}" '
                f'style="width:90%;height:35px;object-fit:cover;object-position:50% 50%;'
                f'opacity:.9;pointer-events:none;">'
                f'</div>',
                unsafe_allow_html=True,
            )
        if u:
            uid, role, school = u.user_id, u.role, u.school_id
        else:
            uid = st.session_state.get("user_id", "guest")
            role, school = "unknown", "default"

        logo_path = cfg.get_logo_path(school)
        if logo_path:
            avatar_html = (
                f'<img src="data:image/png;base64,{_encode_logo(logo_path)}" '
                f'style="width:40px;height:40px;border-radius:50%;object-fit:cover;">'
            )
        else:
            avatar_html = (
                f'<div style="'
                f'width:40px;height:40px;border-radius:50%;'
                f'background:linear-gradient(135deg,#667eea,#764ba2);'
                f'display:flex;align-items:center;justify-content:center;'
                f'font-size:18px;font-weight:700;color:#fff;'
                f'">{uid[0].upper()}</div>'
            )

        st.markdown(
            f"""
            <div style="
                background: linear-gradient(135deg, #1e1e2f 0%, #2d2d44 100%);
                border: 1px solid #3d3d5c;
                border-radius: 12px;
                padding: 16px;
                margin-bottom: 8px;
            ">
                <div style="display:flex;align-items:center;gap:10px;margin-bottom:10px;">
                    {avatar_html}
                    <div>
                        <div style="font-size:1em;font-weight:600;color:#f0f0f0;">
                            {uid}
                        </div>
                        <div style="margin-top:2px;">
                            {_role_badge(role)}
                        </div>
                    </div>
                </div>
                <div style="
                    font-size:0.8em;color:#a0a0b8;
                    display:flex;align-items:center;gap:5px;
                ">
                    <span>🏫</span>
                    <span>{cfg.get_layout(school)}</span>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        if st.button("로그아웃", icon=":material/logout:", use_container_width=True):
            logout_user(cfg)
            st.rerun()

        st.markdown("---")


def render_sidebar(cfg: AppConfig) -> SidebarState:
    with st.sidebar:

        # 하단 고정: spacer 마커 + JS로 DOM 직접 조작
        st.markdown(
            '<div class="sidebar-bottom-spacer"></div>',
            unsafe_allow_html=True,
        )
        components.html("""<script>
(function fix() {
    var pd = window.parent.document;
    var spacer = pd.querySelector('.sidebar-bottom-spacer');
    if (!spacer) { setTimeout(fix, 200); return; }

    // spacer → stVerticalBlock 찾기
    var vb = spacer.closest('[data-testid="stVerticalBlock"]');
    if (!vb) return;

    // stSidebarContent 찾기
    var sc = pd.querySelector('[data-testid="stSidebarContent"]');
    if (!sc) return;

    // vb → sc 사이 모든 wrapper를 flex column + flex-grow:1
    var el = vb;
    while (el && el !== sc) {
        el.style.setProperty('display', 'flex', 'important');
        el.style.setProperty('flex-direction', 'column', 'important');
        el.style.setProperty('flex-grow', '1', 'important');
        el = el.parentElement;
    }
    sc.style.setProperty('display', 'flex', 'important');
    sc.style.setProperty('flex-direction', 'column', 'important');

    // spacer의 stVerticalBlock 직계 자식 wrapper만 flex-grow
    var wrapper = spacer;
    while (wrapper && wrapper.parentElement !== vb) wrapper = wrapper.parentElement;
    if (wrapper) wrapper.style.setProperty('flex-grow', '1', 'important');
})();
</script>""", height=0)

        # ── 테스트 모드 ──
        st.markdown("#### 테스트 모드")
        test_mode = st.toggle(
            "MOCK 모드",
            value=False,
            help="외부 API를 호출하지 않고 로컬에서 응답을 시뮬레이션합니다.",
        )

    return SidebarState(
        test_mode=test_mode,
    )
