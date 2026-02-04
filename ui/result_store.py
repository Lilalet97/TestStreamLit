# ui/result_store.py
from __future__ import annotations

import copy
import streamlit as st
from typing import Any, Dict, List, Optional

_NS = "_rs"


def _k(prefix: str, suffix: str) -> str:
    return f"{_NS}:{prefix}:{suffix}"


def init(prefix: str, *, max_history: int = 20) -> None:
    st.session_state.setdefault(_k(prefix, "max_history"), int(max_history))
    st.session_state.setdefault(_k(prefix, "history"), [])      # List[dict]
    st.session_state.setdefault(_k(prefix, "last"), None)       # dict | None
    st.session_state.setdefault(_k(prefix, "inflight"), None)   # dict | None


def push(prefix: str, item: Dict[str, Any], *, set_last: bool = True) -> None:
    if not isinstance(item, dict):
        raise TypeError("result_store.push: item must be dict")

    it = copy.deepcopy(item)
    it.setdefault("ts", "")
    it.setdefault("kind", "raw")
    it.setdefault("run_id", "")
    it.setdefault("job_id", "")
    it.setdefault("message", "")

    hist: List[Dict[str, Any]] = st.session_state.get(_k(prefix, "history"), [])
    hist.insert(0, it)

    max_history = int(st.session_state.get(_k(prefix, "max_history"), 20))
    st.session_state[_k(prefix, "history")] = hist[:max_history]

    if set_last:
        st.session_state[_k(prefix, "last")] = it


def set_inflight(prefix: str, **info: Any) -> None:
    st.session_state[_k(prefix, "inflight")] = dict(info)


def update_inflight(prefix: str, **info: Any) -> None:
    cur = st.session_state.get(_k(prefix, "inflight")) or {}
    cur.update(info)
    st.session_state[_k(prefix, "inflight")] = cur


def clear_inflight(prefix: str) -> None:
    st.session_state[_k(prefix, "inflight")] = None


def get_last(prefix: str) -> Optional[Dict[str, Any]]:
    return st.session_state.get(_k(prefix, "last"))


def get_history(prefix: str) -> List[Dict[str, Any]]:
    return st.session_state.get(_k(prefix, "history"), [])


def render(
    prefix: str,
    *,
    max_items: int = 5,
    title: Optional[str] = "📌 이전 결과(세션 유지)",
    show_history: bool = True,
    show_clear: bool = True,
    show_inflight: bool = True,
) -> None:
    inflight = st.session_state.get(_k(prefix, "inflight"))
    last = st.session_state.get(_k(prefix, "last"))
    hist = st.session_state.get(_k(prefix, "history"), [])

    if show_inflight and inflight:
        with st.container():
            st.warning("⏳ 작업 진행 정보(세션 유지)")
            st.json(inflight)

    if not last and not hist:
        return

    if title:
        st.markdown(f"### {title}")

    if last:
        _render_item(last)

    if show_history and hist:
        with st.expander("🗂️ 히스토리", expanded=False):
            for item in hist[:max_items]:
                st.divider()
                _render_item(item)

    if show_clear:
        if st.button("🧹 이 탭 결과 지우기", key=f"{_NS}:clear:{prefix}"):
            st.session_state[_k(prefix, "history")] = []
            st.session_state[_k(prefix, "last")] = None
            st.session_state[_k(prefix, "inflight")] = None
            st.rerun()


def _render_item(item: Dict[str, Any]) -> None:
    # ✅ blocks가 있으면: “예전 화면처럼” 그대로 재생
    blocks = item.get("blocks")
    if isinstance(blocks, list) and blocks:
        _render_blocks(blocks)
        return

    # 기존 요약 렌더 (fallback)
    ts = item.get("ts", "")
    run_id = item.get("run_id", "")
    job_id = item.get("job_id", "")
    kind = (item.get("kind") or "raw").lower()
    msg = item.get("message") or ""

    st.caption(f"{ts} | run_id={run_id} | job_id={job_id}")

    if msg:
        st.write(msg)

    if kind == "images":
        for u in (item.get("urls") or []):
            st.image(u)
    elif kind == "video":
        url = item.get("url")
        if url:
            st.video(url)
    elif kind == "error":
        st.error(msg or "error")
        with st.expander("details", expanded=False):
            st.json(item.get("raw") or item)
    else:
        st.json(item.get("raw") or item)


def _render_blocks(blocks: List[Dict[str, Any]]) -> None:
    for b in blocks:
        if not isinstance(b, dict):
            continue
        t = (b.get("t") or "").lower()

        if t == "success":
            st.success(b.get("msg", ""))
        elif t == "info":
            st.info(b.get("msg", ""))
        elif t == "warning":
            st.warning(b.get("msg", ""))
        elif t == "error":
            st.error(b.get("msg", ""))
        elif t == "markdown":
            st.markdown(b.get("body", ""))
        elif t == "write":
            st.write(b.get("body", ""))
        elif t == "caption":
            st.caption(b.get("msg", ""))
        elif t == "code":
            st.code(b.get("body", ""), language=b.get("lang"))
        elif t == "json":
            st.json(b.get("obj"))
        elif t == "images":
            for u in (b.get("urls") or []):
                st.image(u)
        elif t == "video":
            url = b.get("url")
            if url:
                st.video(url)
        elif t == "divider":
            st.divider()
        elif t == "expander":
            label = b.get("label", "details")
            expanded = bool(b.get("expanded", False))
            with st.expander(label, expanded=expanded):
                inner = b.get("blocks")
                if isinstance(inner, list):
                    _render_blocks(inner)
                else:
                    if "obj" in b:
                        st.json(b.get("obj"))
                    elif "body" in b:
                        st.write(b.get("body"))
        else:
            # 알 수 없는 블록은 json으로 안전하게 표시
            st.json(b)
