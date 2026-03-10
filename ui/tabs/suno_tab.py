# ui/tabs/suno_tab.py
"""Suno 탭 — 배정된 Suno 계정 정보 표시 + 웹사이트 열기 버튼."""
import streamlit as st

from core.config import AppConfig
from core.db import get_user_suno_account_id
from ui.sidebar import SidebarState


def render_suno_tab(cfg: AppConfig, sidebar: SidebarState):
    st.markdown(
        """<style>
        .stMainBlockContainer {
            padding: 3.5rem 2rem 1rem 2rem !important;
            max-width: 900px !important;
        }
        </style>""",
        unsafe_allow_html=True,
    )

    user_id = st.session_state.get("user_id", "guest")
    if not st.session_state.get("auth_logged_in") or user_id == "guest":
        st.warning("로그인이 필요합니다.")
        return

    suno_id = get_user_suno_account_id(cfg, user_id)
    account = cfg.get_suno_account(suno_id) if suno_id else None

    if suno_id == 0 or not account:
        st.info("배정된 Suno 계정이 없습니다. 관리자에게 문의하세요.")
    else:
        email = account.get("email", "")
        password = account.get("password", "")
        memo = account.get("memo", "")

        st.markdown(
            f"""
            <style>
            .suno-card {{
                --suno-bg: linear-gradient(135deg, #1e1e2f 0%, #2d2d44 100%);
                --suno-border: #3d3d5c;
                --suno-title: #f0f0f0;
                --suno-label: #a0a0b8;
                --suno-value: #f0f0f0;
                --suno-code: #7dd3fc;
                --suno-memo: #888;
            }}
            @media (prefers-color-scheme: light) {{
                .suno-card {{
                    --suno-bg: linear-gradient(135deg, #e2e6ee 0%, #d8dce6 100%);
                    --suno-border: #b8bfcc;
                    --suno-title: #1a1a2e;
                    --suno-label: #555;
                    --suno-value: #1a1a2e;
                    --suno-code: #0369a1;
                    --suno-memo: #777;
                }}
            }}
            </style>
            <div class="suno-card" style="
                background: var(--suno-bg);
                border: 1px solid var(--suno-border);
                border-radius: 16px;
                padding: 28px 32px;
                margin-bottom: 24px;
            ">
                <div style="font-size:1.3em; font-weight:700; color:var(--suno-title); margin-bottom:18px;">
                    🎵 Suno 계정 정보
                </div>
                <div style="margin-bottom:12px;">
                    <span style="color:var(--suno-label); font-size:0.85em;">계정 번호</span><br>
                    <span style="color:var(--suno-value); font-size:1.05em; font-weight:600;">#{suno_id}</span>
                    {f'<span style="color:var(--suno-memo); font-size:0.85em; margin-left:8px;">({memo})</span>' if memo else ''}
                </div>
                <div style="margin-bottom:12px;">
                    <span style="color:var(--suno-label); font-size:0.85em;">이메일</span><br>
                    <code style="color:var(--suno-code); font-size:1.05em;">{email}</code>
                </div>
                <div>
                    <span style="color:var(--suno-label); font-size:0.85em;">비밀번호</span><br>
                    <code style="color:var(--suno-code); font-size:1.05em;">{password}</code>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.link_button(
        "🎵 Suno 열기",
        "https://suno.com",
        width='stretch',
        type="primary",
    )


TAB = {
    "tab_id": "suno",
    "title": "🎵 Suno",
    "required_features": {"tab.suno"},
    "render": render_suno_tab,
}
