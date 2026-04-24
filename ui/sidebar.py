# ui/sidebar.py
import base64
import html as html_mod
import streamlit as st
import streamlit.components.v1 as components
from dataclasses import dataclass

from pathlib import Path

from core.config import AppConfig
from core.auth import current_user, logout_user
from core.db import get_user_balance

_LOGO_DIR = Path(__file__).resolve().parent.parent / "Sources"


def _inject_sidebar_gap_fix():
    """사이드바 상단 빈 공간(stSidebarHeader 등)을 제거하는 CSS 주입."""
    st.markdown(
        '<style>'
        'section[data-testid="stSidebar"]{padding-bottom:0!important}'
        '[data-testid="stSidebarContent"]{padding-top:0!important;padding-bottom:0!important}'
        '[data-testid="stSidebarUserContent"]{padding-top:0!important;padding-bottom:0!important}'
        '[data-testid="stSidebarHeader"]{display:none!important}'
        '[data-testid="stSidebarContent"]>div'
        ':not([data-testid="stSidebarHeader"])'
        ':not([data-testid="stSidebarUserContent"]){display:none!important}'
        'section[data-testid="stSidebar"] .stHtml:has(iframe[height="0"]){'
        'position:absolute!important;width:0!important;height:0!important;'
        'overflow:hidden!important;pointer-events:none!important}'
        'section[data-testid="stSidebar"] .stCustomComponentV1:has(iframe[style*="height: 1px"]){'
        'position:absolute!important;width:0!important;height:0!important;'
        'overflow:hidden!important;pointer-events:none!important}'
        'section[data-testid="stSidebar"] .stElementContainer[height="0px"],'
        'section[data-testid="stSidebar"] [data-testid="stElementContainer"][height="0px"]{'
        'display:none!important}'
        'section[data-testid="stSidebar"] .stElementContainer:has(iframe[height="0"]){'
        'display:none!important}'
        'section[data-testid="stSidebar"] .stElementContainer:has(iframe[style*="height: 0px"]){'
        'display:none!important}'
        'section[data-testid="stSidebar"] .stElementContainer:has(iframe[style*="height: 1px"]){'
        'display:none!important}'
        '[data-testid="stSidebarUserContent"]>div[data-testid="stVerticalBlock"]'
        '{gap:0!important}'
        '[data-testid="stSidebarUserContent"] .stMarkdown:empty'
        '{display:none!important}'
        '</style>',
        unsafe_allow_html=True,
    )


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

    # ── 사이드바 상단 빈 공간 제거 (메인 영역에서 주입 → 사이드바에 빈 div 안 생김) ──
    _inject_sidebar_gap_fix()

    with st.sidebar:
        # ── 플로팅 알림 배너 (로고 앞 — 빈 컨테이너가 최상단에 위치) ──
        from ui.floating_notice import render_floating_notice
        render_floating_notice(cfg)

        # ── 회사 로고 + 학교 배너 전환 ──
        _logo_dark = _LOGO_DIR / "aimz_BI_logo_edu_white.png"
        _logo_light = _LOGO_DIR / "aimz_BI_logo_edu_edu.png"
        if _logo_dark.exists() and _logo_light.exists():
            _b64_dark = _encode_logo(str(_logo_dark))
            _b64_light = _encode_logo(str(_logo_light))
            _img_style = 'width:100%;height:55px;object-fit:cover;object-position:50% 52%;opacity:.9;pointer-events:none;'

            # 학교 메인 배너 확인
            _school_id = u.school_id if u else st.session_state.get("school_id", "default")
            _branding = cfg.get_branding(_school_id)
            _main_path = _branding.get("main_path", "")
            _has_banner = _main_path and Path(_main_path).exists()

            if _has_banner:
                _b64_main = _encode_logo(_main_path)
                _cycle = 20
                _total = _cycle * 2
                st.markdown(
                    f'<style>'
                    f'@keyframes logo-swap{{'
                    f'  0%,45%{{opacity:1}} 50%,95%{{opacity:0}} 100%{{opacity:1}}'
                    f'}}'
                    f'@keyframes banner-swap{{'
                    f'  0%,45%{{opacity:0}} 50%,95%{{opacity:1}} 100%{{opacity:0}}'
                    f'}}'
                    f'.aimz-logo-dark{{display:block}}'
                    f'.aimz-logo-light{{display:none}}'
                    f'@media(prefers-color-scheme:light){{'
                    f'.aimz-logo-dark{{display:none}}'
                    f'.aimz-logo-light{{display:block}}'
                    f'}}'
                    f'</style>'
                    f'<div style="position:relative;overflow:hidden;height:55px;margin:0 0 40px 0;pointer-events:none;">'
                    f'<div style="position:absolute;inset:0;animation:logo-swap {_total}s ease-in-out infinite;">'
                    f'<img class="aimz-logo-dark" src="data:image/png;base64,{_b64_dark}" style="{_img_style}">'
                    f'<img class="aimz-logo-light" src="data:image/png;base64,{_b64_light}" style="{_img_style}">'
                    f'</div>'
                    f'<div style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center;'
                    f'animation:banner-swap {_total}s ease-in-out infinite;">'
                    f'<img src="data:image/png;base64,{_b64_main}" style="{_img_style}background:#fff;border-radius:4px;">'
                    f'</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    f'<style>'
                    f'.aimz-logo-dark{{display:block}}'
                    f'.aimz-logo-light{{display:none}}'
                    f'@media(prefers-color-scheme:light){{'
                    f'.aimz-logo-dark{{display:none}}'
                    f'.aimz-logo-light{{display:block}}'
                    f'}}</style>'
                    f'<div style="overflow:hidden;height:55px;margin:0 0 40px 0;pointer-events:none;">'
                    f'<img class="aimz-logo-dark" src="data:image/png;base64,{_b64_dark}" style="{_img_style}">'
                    f'<img class="aimz-logo-light" src="data:image/png;base64,{_b64_light}" style="{_img_style}">'
                    f'</div>',
                    unsafe_allow_html=True,
                )

        if u:
            uid, role, school, nickname = u.user_id, u.role, u.school_id, u.nickname
        else:
            uid = st.session_state.get("user_id", "guest")
            role, school, nickname = "unknown", "default", ""
        uid_safe = html_mod.escape(uid, quote=True)
        nick_safe = html_mod.escape(nickname, quote=True) if nickname else ""

        logo_path = cfg.get_logo_path(school)
        if logo_path:
            avatar_html = (
                f'<img src="data:image/png;base64,{_encode_logo(logo_path)}" '
                f'style="width:40px;height:40px;border-radius:50%;object-fit:cover;'
                f'border:1px solid rgba(128,128,128,0.3);background:#fff;">'
            )
        else:
            avatar_html = (
                f'<div style="'
                f'width:40px;height:40px;border-radius:50%;'
                f'background:linear-gradient(135deg,#667eea,#764ba2);'
                f'display:flex;align-items:center;justify-content:center;'
                f'font-size:18px;font-weight:700;color:#fff;'
                f'">{uid_safe[0].upper()}</div>'
            )

        display_main = nick_safe if nick_safe else uid_safe
        id_suffix = f' <span style="font-size:0.8em;color:var(--card-sub);font-weight:400;">({uid_safe})</span>' if nick_safe else ''
        role_badge = _role_badge(role)
        school_name = cfg.get_layout(school)

        card_html = (
            f'<style>'
            f'.sb-profile-card{{'
            f'--card-bg:linear-gradient(135deg,#1e1e2f 0%,#2d2d44 100%);'
            f'--card-border:#3d3d5c;--card-text:#f0f0f0;--card-sub:#a0a0b8;}}'
            f'@media(prefers-color-scheme:light){{'
            f'.sb-profile-card{{'
            f'--card-bg:linear-gradient(135deg,#e2e6ee 0%,#d8dce6 100%);'
            f'--card-border:#b8bfcc;--card-text:#1a1a2e;--card-sub:#555;}}'
            f'}}</style>'
            f'<div class="sb-profile-card" style="'
            f'background:var(--card-bg);border:1px solid var(--card-border);'
            f'border-radius:12px;padding:16px;margin-bottom:8px;">'
            f'<div style="display:flex;align-items:center;gap:10px;margin-bottom:10px;">'
            f'{avatar_html}'
            f'<div>'
            f'<div style="font-size:1em;font-weight:600;color:var(--card-text);">{display_main}{id_suffix}</div>'
            f'<div style="margin-top:2px;">{role_badge}</div>'
            f'</div></div>'
            f'<div style="font-size:0.8em;color:var(--card-sub);display:flex;align-items:center;gap:5px;">'
            f'<span>🏫</span><span>{school_name}</span>'
            f'</div></div>'
        )
        st.markdown(card_html, unsafe_allow_html=True)

        if u and u.role in ("teacher", "student"):
            if st.button("프로필 설정", icon=":material/settings:", width="stretch", key="sb_profile_settings"):
                st.session_state["_open_profile_settings"] = True
                st.rerun()

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
            _school_id = st.session_state.get("school_id", "default")
            _features = set(cfg.get_enabled_features(_school_id))

            # 학교에 활성화된 탭만 크레딧 정보에 표시
            _lines = []
            if "tab.gpt" in _features:
                _lines.append("GPT: 1/메시지")
            if "tab.mj" in _features:
                _lines.append("MJ: 8/4장 (Relax)")
            if "tab.mj_free" in _features:
                _lines.append("MJ Free: 무료")
            if "tab.mj_paid" in _features:
                _lines.append("MJ Fast: 8/4장")
                _lines.append("MJ Turbo: 16/4장 (2배)")
            if any(f in _features for f in ("tab.mj", "tab.mj_free", "tab.mj_paid")):
                _lines.append("Describe: 1")
            if "tab.nanobanana" in _features:
                _lines.append("NB: 5/장")
            if "tab.nanobanana_2" in _features:
                _lines.append("NB 2: 5/장")
            if "tab.nanobanana_pro" in _features:
                _lines.append("NB Pro: 10/장")
            if "tab.kling" in _features:
                _lines.append("Kling: 7/초")
            if "tab.kling_veo" in _features:
                _lines.append("Veo: 7/초")
            if "tab.kling_grok" in _features:
                _lines.append("Grok: 7/초")
            if "tab.kling_ltx" in _features:
                _lines.append("LTX: 7/초")
            if "tab.elevenlabs" in _features:
                _lines.append("TTS: 5 · VTV: 10 · SFX: 2 · Clone: 무료")

            _cost_text = "<br>".join(_lines) if _lines else ""

            _cost_info = (
                '<div class="credit-cost-info" style="font-size:0.7em;margin-top:4px;line-height:1.4;">'
                f'{_cost_text}'
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
    // gap-fix CSS는 _inject_sidebar_gap_fix()에서 처리
})();
</script>""", height=0)

        # ── 에러 이력 ──
        _cu = current_user()
        if _cu and _cu.role in ("teacher", "student"):
            from core.db import load_error_log, count_unseen_replies
            _errors = load_error_log(cfg, _cu.user_id, limit=20)
            _err_count = len(_errors)
            _btn_label = f"최근 에러 ({_err_count}건)" if _err_count else "에러 없음"
            if st.button(_btn_label, key="sb_error_log", width="stretch"):
                st.session_state["_open_error_log"] = True
                st.rerun()

            # ── 관리자 문의 ──
            _unseen = count_unseen_replies(cfg, _cu.user_id)
            _sup_label = f"문의하기 🔴 {_unseen}" if _unseen > 0 else "문의하기"
            if st.button(_sup_label, key="sb_support", width="stretch", icon=":material/chat:"):
                # 버튼 클릭 = 확인 의도 → 모든 미확인 답변 seen 처리
                if _unseen > 0:
                    from core.db import mark_all_replies_seen
                    try:
                        mark_all_replies_seen(cfg, _cu.user_id)
                    except Exception:
                        pass
                st.session_state["_open_support"] = True
                st.rerun()

        # ── 테스트 모드 ──
        test_mode = st.toggle(
            "MOCK 모드",
            value=False,
            help="외부 API를 호출하지 않고 로컬에서 응답을 시뮬레이션합니다.",
        )

    # ── dialog 렌더 (sidebar 밖) ──
    if st.session_state.pop("_open_error_log", False):
        _show_error_log_dialog(cfg)
    if st.session_state.pop("_open_profile_settings", False):
        _show_profile_settings_dialog(cfg)
    if st.session_state.pop("_open_support", False):
        _show_support_dialog(cfg)

    return SidebarState(
        test_mode=test_mode,
    )


@st.dialog("에러 이력", width="large")
def _show_error_log_dialog(cfg: AppConfig):
    from core.db import load_error_log
    _cu = current_user()
    if not _cu:
        st.warning("로그인이 필요합니다.")
        return

    errors = load_error_log(cfg, _cu.user_id, limit=20)
    if not errors:
        st.info("최근 에러가 없습니다.")
        return

    import pandas as pd
    from datetime import datetime, timedelta, timezone
    _KST = timezone(timedelta(hours=9))

    for e in errors:
        try:
            dt = datetime.fromisoformat(e["created_at"].replace("Z", "+00:00"))
            e["created_at"] = dt.astimezone(_KST).strftime("%m/%d %H:%M")
        except Exception:
            pass

    df = pd.DataFrame(errors)
    df = df.drop(columns=["id"], errors="ignore")
    df.columns = ["기능", "에러 내용", "발생 시각"]
    st.dataframe(df, width="stretch", hide_index=True)

    if st.button("🗑 전체 삭제", key="_err_clear_all", type="secondary"):
        from core.db import clear_error_log
        try:
            n = clear_error_log(cfg, _cu.user_id)
            st.success(f"{n}건의 에러 이력을 삭제했습니다.")
            st.rerun()
        except Exception as e:
            st.error(f"삭제 실패: {e}")


@st.dialog("프로필 설정", width="large")
def _show_profile_settings_dialog(cfg: AppConfig):
    from core.db import update_user_fields, set_user_password, get_user
    from core.auth import hash_password, verify_password
    _cu = current_user()
    if not _cu:
        st.warning("로그인이 필요합니다.")
        return

    st.markdown("#### 닉네임 변경")
    current_nick = _cu.nickname or ""
    new_nick = st.text_input("닉네임", value=current_nick, key="_pf_nickname")
    if st.button("닉네임 저장", key="_pf_save_nick", width="stretch"):
        new_nick = (new_nick or "").strip()
        if not new_nick:
            st.error("닉네임을 입력해주세요.")
        elif new_nick == current_nick:
            st.info("변경 사항이 없습니다.")
        else:
            try:
                update_user_fields(cfg, _cu.user_id, nickname=new_nick)
                st.session_state["auth_nickname"] = new_nick
                st.success("닉네임이 변경되었습니다.")
                st.rerun()
            except Exception as e:
                st.error(f"저장 실패: {e}")

    st.markdown("---")
    st.markdown("#### 비밀번호 변경")
    cur_pw = st.text_input("현재 비밀번호", type="password", key="_pf_cur_pw")
    new_pw = st.text_input("새 비밀번호", type="password", key="_pf_new_pw")
    new_pw2 = st.text_input("새 비밀번호 확인", type="password", key="_pf_new_pw2")
    if st.button("비밀번호 변경", key="_pf_save_pw", width="stretch"):
        if not (cur_pw and new_pw and new_pw2):
            st.error("모든 필드를 입력해주세요.")
        elif new_pw != new_pw2:
            st.error("새 비밀번호가 일치하지 않습니다.")
        elif len(new_pw) < 4:
            st.error("비밀번호는 최소 4자 이상이어야 합니다.")
        else:
            row = get_user(cfg, _cu.user_id)
            if not row or not verify_password(cur_pw, row["password_hash"]):
                st.error("현재 비밀번호가 일치하지 않습니다.")
            else:
                try:
                    set_user_password(cfg, _cu.user_id, hash_password(new_pw))
                    st.success("비밀번호가 변경되었습니다.")
                except Exception as e:
                    st.error(f"저장 실패: {e}")


@st.dialog("관리자 문의", width="large")
def _show_support_dialog(cfg: AppConfig):
    from core.db import (
        create_support_ticket,
        list_support_tickets_for_user,
        mark_ticket_seen,
        delete_support_ticket,
    )
    from datetime import datetime, timedelta, timezone as _tz
    _KST = _tz(timedelta(hours=9))
    _cu = current_user()
    if not _cu:
        st.warning("로그인이 필요합니다.")
        return

    # 새 문의
    st.markdown("#### 새 문의 작성")
    subj = st.text_input("제목", key="_sup_subj", max_chars=100)
    msg = st.text_area("내용", key="_sup_msg", height=120, max_chars=5000)
    if st.button("문의 전송", key="_sup_send", width="stretch", type="primary"):
        if not subj.strip() or not msg.strip():
            st.error("제목과 내용을 입력해주세요.")
        else:
            try:
                create_support_ticket(cfg, _cu.user_id, _cu.school_id or "", subj, msg)
                st.success("문의가 전송되었습니다.")
                st.rerun()
            except Exception as e:
                st.error(f"전송 실패: {e}")

    st.markdown("---")
    st.markdown("#### 내 문의 내역")
    tickets = list_support_tickets_for_user(cfg, _cu.user_id, limit=50)
    if not tickets:
        st.info("문의 내역이 없습니다.")
        return

    for t in tickets:
        try:
            _dt = datetime.fromisoformat(t["created_at"].replace("Z", "+00:00")).astimezone(_KST).strftime("%m/%d %H:%M")
        except Exception:
            _dt = t.get("created_at", "")
        has_reply = bool(t.get("reply"))
        unseen = has_reply and not int(t.get("user_seen_reply") or 0)
        badge = "🔴 답변 도착" if unseen else ("✅ 답변 완료" if has_reply else "⏳ 대기 중")
        with st.expander(f"{badge} · {t['subject']} · {_dt}", expanded=unseen):
            st.markdown(f"**내용**  \n{t['message']}")
            if has_reply:
                try:
                    _rdt = datetime.fromisoformat(t["reply_at"].replace("Z", "+00:00")).astimezone(_KST).strftime("%m/%d %H:%M")
                except Exception:
                    _rdt = t.get("reply_at", "")
                st.markdown(f"---\n**관리자 답변** ({_rdt})  \n{t['reply']}")
                if unseen:
                    try:
                        mark_ticket_seen(cfg, int(t["ticket_id"]), _cu.user_id)
                    except Exception:
                        pass
            _, col2 = st.columns([4, 1])
            with col2:
                if st.button("삭제", key=f"_sup_del_{t['ticket_id']}"):
                    try:
                        delete_support_ticket(cfg, int(t["ticket_id"]), _cu.user_id)
                        st.rerun()
                    except Exception as e:
                        st.error(f"삭제 실패: {e}")
