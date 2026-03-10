# ui/sidebar.py
import base64
import streamlit as st
import streamlit.components.v1 as components
from dataclasses import dataclass

from pathlib import Path

from core.config import AppConfig
from core.auth import current_user, logout_user
from core.db import get_user_balance

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
        # ── 회사 로고 (다크/라이트 자동 전환) ──
        _logo_dark = _LOGO_DIR / "aimz_BI_logo_edu_white.png"
        _logo_light = _LOGO_DIR / "aimz_BI_logo_edu_edu.png"
        if _logo_dark.exists() and _logo_light.exists():
            _b64_dark = _encode_logo(str(_logo_dark))
            _b64_light = _encode_logo(str(_logo_light))
            _img_style = 'width:100%;height:55px;object-fit:cover;object-position:50% 52%;opacity:.9;pointer-events:none;'
            st.markdown(
                f'<style>'
                f'.aimz-logo-dark{{display:block}}'
                f'.aimz-logo-light{{display:none}}'
                f'@media(prefers-color-scheme:light){{'
                f'.aimz-logo-dark{{display:none}}'
                f'.aimz-logo-light{{display:block}}'
                f'}}</style>'
                f'<div style="overflow:hidden;height:55px;margin:0 0 12px 0;pointer-events:none;">'
                f'<img class="aimz-logo-dark" src="data:image/png;base64,{_b64_dark}" style="{_img_style}">'
                f'<img class="aimz-logo-light" src="data:image/png;base64,{_b64_light}" style="{_img_style}">'
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
            <style>
            .sb-profile-card {{
                --card-bg: linear-gradient(135deg, #1e1e2f 0%, #2d2d44 100%);
                --card-border: #3d3d5c;
                --card-text: #f0f0f0;
                --card-sub: #a0a0b8;
            }}
            @media (prefers-color-scheme: light) {{
                .sb-profile-card {{
                    --card-bg: linear-gradient(135deg, #e2e6ee 0%, #d8dce6 100%);
                    --card-border: #b8bfcc;
                    --card-text: #1a1a2e;
                    --card-sub: #555;
                }}
            }}
            </style>
            <div class="sb-profile-card" style="
                background: var(--card-bg);
                border: 1px solid var(--card-border);
                border-radius: 12px;
                padding: 16px;
                margin-bottom: 8px;
            ">
                <div style="display:flex;align-items:center;gap:10px;margin-bottom:10px;">
                    {avatar_html}
                    <div>
                        <div style="font-size:1em;font-weight:600;color:var(--card-text);">
                            {uid}
                        </div>
                        <div style="margin-top:2px;">
                            {_role_badge(role)}
                        </div>
                    </div>
                </div>
                <div style="
                    font-size:0.8em;color:var(--card-sub);
                    display:flex;align-items:center;gap:5px;
                ">
                    <span>🏫</span>
                    <span>{cfg.get_layout(school)}</span>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        if st.button("로그아웃", icon=":material/logout:", width="stretch"):
            logout_user(cfg)
            st.rerun()

        st.markdown("---")


def render_sidebar(cfg: AppConfig) -> SidebarState:
    with st.sidebar:

        # ── 크레딧 잔액 (통합) ──
        _role = st.session_state.get("auth_role", "")
        _uid = st.session_state.get("auth_user_id", "")
        if _role not in ("admin", "teacher", "") and _uid:
            _balance = get_user_balance(cfg, _uid)
            _cost_info = (
                '<div class="credit-cost-info" style="font-size:0.7em;margin-top:4px;line-height:1.4;">'
                'GPT 1 · NB 5 · EL 5 · MJ 20(4장)<br>'
                'Kling 7/초 · Grok 7/초 · Veo 7/초'
                '</div>'
            )
            st.markdown(
                f'<style>'
                f'.sb-credit-box{{'
                f'  --cr-bg:#2d2d44;--cr-border:#3d3d5c;'
                f'  --cr-title:#a0a0b8;--cr-label:#e0e0e0;'
                f'  --cr-value:#f8c537;--cr-info:#888}}'
                f'@media(prefers-color-scheme:light){{'
                f'.sb-credit-box{{'
                f'  --cr-bg:#e2e6ee;--cr-border:#b8bfcc;'
                f'  --cr-title:#555;--cr-label:#1a1a2e;'
                f'  --cr-value:#996515;--cr-info:#777}}}}'
                f'</style>'
                f'<div class="sb-credit-box" style="background:var(--cr-bg);border:1px solid var(--cr-border);'
                f'border-radius:8px;padding:6px 10px;margin-bottom:8px;">'
                f'<div style="display:flex;justify-content:space-between;align-items:center;">'
                f'<span style="font-size:0.8em;color:var(--cr-title);">크레딧 잔액</span>'
                f'<span style="font-size:1.1em;font-weight:bold;color:var(--cr-value);">{_balance}</span>'
                f'</div>'
                f'{_cost_info}</div>'
                f'<style>.credit-cost-info{{color:var(--cr-info)}}</style>',
                unsafe_allow_html=True,
            )

        # ── MOCK 토글을 화면 최하단에 고정 (flex spacer) ──
        st.markdown(
            '<div class="sidebar-bottom-spacer"></div>',
            unsafe_allow_html=True,
        )
        components.html("""<script>
(function fix(){
    var pd=window.parent.document;
    var spacer=pd.querySelector('.sidebar-bottom-spacer');
    if(!spacer){setTimeout(fix,200);return}
    var vb=spacer.closest('[data-testid="stVerticalBlock"]');
    if(!vb)return;
    var sc=pd.querySelector('[data-testid="stSidebarContent"]');
    if(!sc)return;
    var el=vb;
    while(el&&el!==sc){
        el.style.setProperty('display','flex','important');
        el.style.setProperty('flex-direction','column','important');
        el.style.setProperty('flex-grow','1','important');
        el=el.parentElement;
    }
    sc.style.setProperty('display','flex','important');
    sc.style.setProperty('flex-direction','column','important');
    var w=spacer;
    while(w&&w.parentElement!==vb) w=w.parentElement;
    if(w) w.style.setProperty('flex-grow','1','important');
    // MOCK 토글 → 사이드바까지 모든 부모의 하단 여백 제거
    var labels=sc.querySelectorAll('label');
    for(var li=0;li<labels.length;li++){
        if(labels[li].textContent.indexOf('MOCK')!==-1){
            var p=labels[li];
            while(p&&p!==sc){
                p.style.setProperty('padding-bottom','0','important');
                p.style.setProperty('margin-bottom','0','important');
                p=p.parentElement;
            }
            sc.style.setProperty('padding-bottom','0','important');
            break;
        }
    }
    // height=0 iframe / 1px 컴포넌트 래퍼 간격 제거
    if(!pd.getElementById('sidebar-gap-fix')){
        var s=pd.createElement('style');s.id='sidebar-gap-fix';
        s.textContent=
            'section[data-testid="stSidebar"]{padding-bottom:0!important}'+
            '[data-testid="stSidebarContent"]{padding-bottom:0!important}'+
            '[data-testid="stSidebarUserContent"]{padding-bottom:0!important}'+
            '[data-testid="stSidebarContent"]>div:not([data-testid="stSidebarHeader"]):not([data-testid="stSidebarUserContent"]){'+
                'display:none!important}'+
            'section[data-testid="stSidebar"] .stHtml:has(iframe[height="0"]){'+
                'position:absolute!important;width:0!important;height:0!important;'+
                'overflow:hidden!important;pointer-events:none!important}'+
            'section[data-testid="stSidebar"] .stCustomComponentV1:has(iframe[style*="height: 1px"]){'+
                'position:absolute!important;width:0!important;height:0!important;'+
                'overflow:hidden!important;pointer-events:none!important}';
        pd.head.appendChild(s);
    }
})();
</script>""", height=0)

        # ── 테스트 모드 ──
        test_mode = st.toggle(
            "MOCK 모드",
            value=False,
            help="외부 API를 호출하지 않고 로컬에서 응답을 시뮬레이션합니다.",
        )

    return SidebarState(
        test_mode=test_mode,
    )
