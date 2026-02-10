# ui/sidebar.py
import base64
from datetime import datetime, timezone, timedelta
import streamlit as st
from dataclasses import dataclass
from typing import Callable

_KST = timezone(timedelta(hours=9))

from core.config import AppConfig
from core.db import list_runs, count_active_jobs, clear_my_active_jobs
from core.auth import current_user, logout_user


@dataclass
class SidebarState:
    session_only: bool
    test_mode: bool
    mock_scenario: str
    refresh_counts: Callable[[], None]


def _encode_logo(path: str) -> str:
    """로고 이미지를 base64로 인코딩 (HTML 인라인 사용)."""
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def _role_badge(role: str) -> str:
    colors = {"admin": "#e74c3c", "user": "#3498db"}
    bg = colors.get(role, "#95a5a6")
    return (
        f'<span style="background:{bg};color:#fff;padding:2px 8px;'
        f'border-radius:10px;font-size:0.75em;font-weight:600;'
        f'letter-spacing:0.5px;">{role.upper()}</span>'
    )


def render_sidebar(cfg: AppConfig) -> SidebarState:
    u = current_user()

    with st.sidebar:
        # ── 프로필 카드 ──
        if u:
            uid, role, school = u.user_id, u.role, u.school_id
        else:
            uid = st.session_state.get("user_id", "guest")
            role, school = "unknown", "default"

        # 학교 로고가 있으면 아바타 원 대신 로고 표시
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

        # ── 세션 ──
        st.markdown("#### 세션")
        sid = st.session_state.session_id
        if st.button("새 세션 시작", icon=":material/refresh:", use_container_width=True):
            import uuid
            st.session_state.session_id = str(uuid.uuid4())
            st.rerun()

        st.caption(f"`{sid[:8]}…`")
        st.markdown("---")

        # ── 동시 실행 현황 ──
        st.markdown("#### 동시 실행 현황")

        my_count = count_active_jobs(cfg, st.session_state.user_id)
        all_count = count_active_jobs(cfg, None)

        c1, c2 = st.columns(2)
        c1.metric("내 작업", f"{my_count} / {cfg.user_max_concurrency}")
        c2.metric("전체", f"{all_count} / {cfg.global_max_concurrency}")

        my_active_ph = st.empty()
        all_active_ph = st.empty()

        def refresh_counts():
            mc = count_active_jobs(cfg, st.session_state.user_id)
            ac = count_active_jobs(cfg, None)
            my_active_ph.caption(f"내 작업: {mc} / {cfg.user_max_concurrency}")
            all_active_ph.caption(f"전체: {ac} / {cfg.global_max_concurrency}")

        st.markdown("---")

        # ── 실행 히스토리 ──
        st.markdown("#### 실행 히스토리")
        session_only = st.toggle("현재 세션만", value=False)

        hist = list_runs(cfg, st.session_state.user_id, session_only=session_only, limit=30)

        def _label(r):
            raw = r["created_at"]
            try:
                dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                t = dt.astimezone(_KST).strftime("%m-%d %H:%M")
            except Exception:
                t = raw.replace("T", " ").replace("Z", "")
            state = r["state"] or ""
            icon = {"completed": "✅", "failed": "❌", "running": "⏳"}.get(state, "◻️")
            return f"{icon} {r['provider']}/{r['operation']}  —  {t}"

        if hist:
            options = [r["run_id"] for r in hist]

            prev_sel = st.session_state.get("selected_run_id")
            idx = options.index(prev_sel) if prev_sel in options else 0

            sel = st.selectbox(
                "최근 실행 선택",
                options=options,
                index=idx,
                format_func=lambda rid: _label(next(x for x in hist if x["run_id"] == rid)),
                key="selected_run_id",
                label_visibility="collapsed",
            )

            if st.button("상세 보기", icon=":material/open_in_new:", use_container_width=True):
                st.session_state["_open_run_detail"] = bool(sel)
        else:
            st.info("실행 기록이 아직 없습니다.")
            st.session_state["selected_run_id"] = None
            st.session_state["_open_run_detail"] = False

        st.markdown("---")

        # ── 테스트 모드 ──
        st.markdown("#### 테스트 모드")
        test_mode = st.toggle(
            "MOCK 모드",
            value=False,
            help="외부 API를 호출하지 않고 로컬에서 응답을 시뮬레이션합니다.",
        )
        mock_scenario = "SUCCESS"
        if test_mode:
            mock_scenario = st.selectbox(
                "시나리오",
                ["SUCCESS", "FAILED_402", "FAILED_401", "FAILED_429", "SERVER_500", "TIMEOUT"],
                index=0,
            )

        if st.button("내 활성 작업 강제 정리", icon=":material/delete_sweep:", use_container_width=True):
            clear_my_active_jobs(cfg, session_only=False, only_stale=False)
            st.success("정리 완료!")
            st.rerun()

    return SidebarState(
        session_only=session_only,
        test_mode=test_mode,
        mock_scenario=mock_scenario,
        refresh_counts=refresh_counts,
    )
