# ui/floating_chat.py
"""플로팅 채팅 컴포넌트 — teacher/student 역할용 우측 사이드 채팅 패널."""
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from core.config import AppConfig
from core.auth import current_user
from core.db import insert_chat_message, load_chat_messages

_COMPONENT_DIR = Path(__file__).resolve().parent / "components" / "floating_chat"
_chat_component_func = components.declare_component("floating_chat", path=str(_COMPONENT_DIR))


def _chat_component(messages: list, user_id: str, user_role: str, school_id: str, key: str = "floating_chat"):
    return _chat_component_func(
        messages=messages,
        user_id=user_id,
        user_role=user_role,
        school_id=school_id,
        key=key,
        default=None,
    )


@st.fragment(run_every="3s")
def _chat_fragment(cfg: AppConfig, user_id: str, user_role: str, school_id: str):
    """3초마다 자동 폴링하는 채팅 프래그먼트."""
    messages = load_chat_messages(cfg, school_id, limit=100)

    result = _chat_component(
        messages=messages,
        user_id=user_id,
        user_role=user_role,
        school_id=school_id,
    )

    if not result or not isinstance(result, dict):
        return

    action = result.get("action")
    ts = result.get("ts", 0)

    if ts == st.session_state.get("_chat_last_ts"):
        return
    st.session_state["_chat_last_ts"] = ts

    if action == "send_message":
        msg = (result.get("message") or "").strip()
        if msg:
            insert_chat_message(cfg, school_id, user_id, user_role, msg)
            st.rerun(scope="fragment")


def render_floating_chat(cfg: AppConfig):
    """teacher/student 역할용 플로팅 채팅 렌더링."""
    user = current_user()
    if not user or user.role not in ("teacher", "student"):
        return
    _chat_fragment(cfg, user.user_id, user.role, user.school_id)
