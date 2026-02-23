# ui/tabs/gpt_tab.py
"""GPT Chat 탭 — declare_component 양방향 통신 + Python 경유 OpenAI 호출."""
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests

import streamlit as st
import streamlit.components.v1 as components

from core.config import AppConfig
from core.api_bridge import call_with_lease
from core.db import (
    upsert_gpt_conversation,
    load_gpt_conversations,
    delete_gpt_conversation,
)
from ui.sidebar import SidebarState

_COMPONENT_DIR = Path(__file__).resolve().parent / "templates" / "gpt"
_gpt_component_func = components.declare_component("gpt_component", path=str(_COMPONENT_DIR))


def _gpt_component(conversations: list, active_id: str,
                    default_model: str, is_guest: bool,
                    frame_height: int = 900, key: str = "gpt_main"):
    """GPT 커스텀 컴포넌트 래퍼."""
    return _gpt_component_func(
        conversations=conversations,
        active_id=active_id,
        default_model=default_model,
        is_guest=is_guest,
        frame_height=frame_height,
        key=key,
        default=None,
    )


def _is_authenticated() -> bool:
    return (
        st.session_state.get("auth_logged_in", False)
        and st.session_state.get("user_id", "guest") != "guest"
    )


def _init_state(cfg: AppConfig):
    """세션 상태 초기화: 로그인 사용자는 DB에서 로드."""
    if "gpt_conversations" in st.session_state and st.session_state.get("_gpt_db_loaded"):
        return

    if _is_authenticated():
        convs = load_gpt_conversations(cfg, st.session_state["user_id"])
        if convs:
            st.session_state.gpt_conversations = convs
            st.session_state.gpt_active_id = convs[0]["id"]
            st.session_state["_gpt_db_loaded"] = True
            return

    # 게스트 또는 DB에 데이터 없음 → 빈 대화 1개
    if "gpt_conversations" not in st.session_state:
        new_id = str(uuid.uuid4())
        st.session_state.gpt_conversations = [{
            "id": new_id,
            "title": "New Chat",
            "model": cfg.openai_model,
            "messages": [],
        }]
        st.session_state.gpt_active_id = new_id
    st.session_state["_gpt_db_loaded"] = True


def _auto_title(messages: list) -> str:
    """첫 user 메시지의 앞 30자를 제목으로."""
    for m in messages:
        if m.get("role") == "user" and m.get("content", "").strip():
            text = m["content"].strip()
            return text[:30] + ("..." if len(text) > 30 else "")
    return "New Chat"


def _mock_gpt_response(user_msg: str) -> str:
    """Mock 모드: 모의 GPT 응답."""
    return (
        f"[Mock 응답] 질문을 잘 받았습니다: \"{user_msg[:50]}...\"\n\n"
        "이것은 테스트 모드의 모의 응답입니다. "
        "실제 API를 호출하려면 Mock 모드를 OFF로 설정하세요."
    )


def _call_openai_chat(api_key: str, model: str, messages: list) -> str:
    """OpenAI Chat Completions API 호출 (Python 서버 사이드)."""
    resp = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        json={
            "model": model,
            "messages": [{"role": m["role"], "content": m["content"]} for m in messages],
        },
        timeout=120,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"OpenAI API {resp.status_code}: {resp.text[:300]}")
    return resp.json()["choices"][0]["message"]["content"]


def render_gpt_tab(cfg: AppConfig, sidebar: SidebarState):
    """GPT Chat 탭: declare_component 양방향 통신."""
    _init_state(cfg)

    # ── 대기 중인 메시지 처리 (2단계: 실제 API 호출) ──
    pending = st.session_state.get("_gpt_pending_send")
    if pending:
        del st.session_state["_gpt_pending_send"]
        conv_id = pending["conv_id"]
        model = pending["model"]
        user_message = pending["user_message"]

        for conv in st.session_state.gpt_conversations:
            if conv["id"] == conv_id:
                try:
                    api_msgs = [{"role": m["role"], "content": m["content"]} for m in conv["messages"]]
                    reply = call_with_lease(
                        cfg,
                        test_mode=pending["test_mode"],
                        provider="openai",
                        mock_fn=lambda: _mock_gpt_response(user_message),
                        real_fn=lambda kp: _call_openai_chat(kp["api_key"], model, api_msgs),
                    )
                    conv["messages"].append({
                        "role": "assistant", "content": reply,
                        "ts": datetime.now(timezone.utc).isoformat(),
                    })
                except Exception as e:
                    conv["messages"].append({
                        "role": "assistant", "content": f"[Error] {e}",
                        "ts": datetime.now(timezone.utc).isoformat(),
                    })

                conv["model"] = model
                if conv["title"] == "New Chat" and len(conv["messages"]) >= 2:
                    conv["title"] = _auto_title(conv["messages"])

                if _is_authenticated():
                    try:
                        upsert_gpt_conversation(cfg, st.session_state["user_id"], conv)
                    except Exception:
                        pass
                break
        st.rerun()

    st.markdown(
        """<style>
        .stMainBlockContainer {
            padding:3.5rem 0 0 0 !important;
            max-width:100% !important;
        }
        .stMainBlockContainer > div {gap:0 !important;}
        .stMainBlockContainer iframe {
            width:100% !important;
            height:calc(100vh - 3.5rem) !important;
            display:block !important;
            border:none !important;
        }
        </style>""",
        unsafe_allow_html=True,
    )

    is_guest = not _is_authenticated()

    result = _gpt_component(
        conversations=st.session_state.gpt_conversations,
        active_id=st.session_state.get("gpt_active_id", ""),
        default_model=cfg.openai_model,
        is_guest=is_guest,
        frame_height=900,
    )

    if not result or not isinstance(result, dict):
        return

    action = result.get("action")
    ts = result.get("ts", 0)

    # 중복 실행 방지: 처리 완료된 action key set으로 체크
    _conv_id = result.get("conv_id", "")
    dedup_key = f"{action}_{ts}_{_conv_id}"
    _processed = st.session_state.setdefault("_gpt_processed_actions", set())
    if dedup_key in _processed:
        return
    _processed.add(dedup_key)
    if len(_processed) > 100:
        st.session_state["_gpt_processed_actions"] = {dedup_key}

    # ── send_message: Python에서 OpenAI API 호출 ──
    if action == "send_message":
        # 이미 대기 중인 요청이 있으면 무시 (중복 방지)
        if st.session_state.get("_gpt_pending_send"):
            return

        conv_id = result.get("conv_id")
        user_message = result.get("user_message", "")
        model = result.get("model", cfg.openai_model)

        # 유저 메시지를 대화에 먼저 추가
        for conv in st.session_state.gpt_conversations:
            if conv["id"] == conv_id:
                now = datetime.now(timezone.utc).isoformat()
                conv["messages"].append({"role": "user", "content": user_message, "ts": now})
                break

        # 다음 rerun에서 처리할 대기 요청 저장
        st.session_state["_gpt_pending_send"] = {
            "conv_id": conv_id,
            "user_message": user_message,
            "model": model,
            "test_mode": sidebar.test_mode,
        }
        st.rerun()

    # ── save_conversation: 전체 messages 저장 ──
    elif action == "save_conversation":
        conv_id = result.get("conv_id")
        messages = result.get("messages", [])
        model = result.get("model", cfg.openai_model)

        for conv in st.session_state.gpt_conversations:
            if conv["id"] == conv_id:
                conv["messages"] = messages
                conv["model"] = model
                if conv["title"] == "New Chat" and messages:
                    conv["title"] = _auto_title(messages)
                break

        if _is_authenticated():
            for conv in st.session_state.gpt_conversations:
                if conv["id"] == conv_id:
                    try:
                        upsert_gpt_conversation(cfg, st.session_state["user_id"], conv)
                    except Exception:
                        pass
                    break

        st.rerun()

    # ── new_conversation ──
    elif action == "new_conversation":
        new_id = str(uuid.uuid4())
        new_conv = {
            "id": new_id,
            "title": "New Chat",
            "model": cfg.openai_model,
            "messages": [],
        }
        st.session_state.gpt_conversations.insert(0, new_conv)
        st.session_state.gpt_active_id = new_id
        st.rerun()

    # ── switch_conversation ──
    elif action == "switch_conversation":
        st.session_state.gpt_active_id = result.get("conv_id")
        st.rerun()

    # ── delete_conversation ──
    elif action == "delete_conversation":
        conv_id = result.get("conv_id")
        st.session_state.gpt_conversations = [
            c for c in st.session_state.gpt_conversations if c["id"] != conv_id
        ]
        if _is_authenticated():
            try:
                delete_gpt_conversation(cfg, st.session_state["user_id"], conv_id)
            except Exception:
                pass

        if st.session_state.get("gpt_active_id") == conv_id:
            if st.session_state.gpt_conversations:
                st.session_state.gpt_active_id = st.session_state.gpt_conversations[0]["id"]
            else:
                new_id = str(uuid.uuid4())
                st.session_state.gpt_conversations = [{
                    "id": new_id,
                    "title": "New Chat",
                    "model": cfg.openai_model,
                    "messages": [],
                }]
                st.session_state.gpt_active_id = new_id

        st.rerun()

    # ── rename_conversation ──
    elif action == "rename_conversation":
        conv_id = result.get("conv_id")
        new_title = result.get("title", "")
        for conv in st.session_state.gpt_conversations:
            if conv["id"] == conv_id:
                conv["title"] = new_title
                break
        if _is_authenticated():
            for conv in st.session_state.gpt_conversations:
                if conv["id"] == conv_id:
                    try:
                        upsert_gpt_conversation(cfg, st.session_state["user_id"], conv)
                    except Exception:
                        pass
                    break
        st.rerun()


TAB = {
    "tab_id": "gpt",
    "title": "💬 GPT Chat",
    "required_features": {"tab.gpt"},
    "render": render_gpt_tab,
}
