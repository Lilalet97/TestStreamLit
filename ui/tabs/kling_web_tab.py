# ui/tabs/kling_web_tab.py
"""Kling 비디오 생성 페이지 — declare_component 양방향 통신."""
import time
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from core.config import AppConfig
from core.api_bridge import call_with_lease
from core.db import insert_kling_web_item, load_kling_web_history, update_kling_web_video_urls
from providers import kling
from ui.sidebar import SidebarState

_COMPONENT_DIR = Path(__file__).resolve().parent / "templates" / "kling"
_kling_component_func = components.declare_component("kling_component", path=str(_COMPONENT_DIR))


def _is_authenticated() -> bool:
    return st.session_state.get("auth_logged_in") and st.session_state.get("user_id", "guest") != "guest"


def _init_state(cfg: AppConfig):
    """세션 상태 초기화: 로그인 사용자는 DB에서 로드."""
    if "kling_web_history" in st.session_state and st.session_state.get("_kling_db_loaded"):
        return

    if _is_authenticated():
        items = load_kling_web_history(cfg, st.session_state["user_id"])
        if items:
            st.session_state.kling_web_history = items
            st.session_state["_kling_db_loaded"] = True
            return

    if "kling_web_history" not in st.session_state:
        st.session_state.kling_web_history = []
    st.session_state["_kling_db_loaded"] = True


def _kling_component(
    frame_height: int = 900,
    history: list | None = None,
    key: str = "kling_main",
):
    """Kling 커스텀 컴포넌트 래퍼."""
    return _kling_component_func(
        frame_height=frame_height,
        history=history or [],
        key=key,
        default=None,
    )


def _call_kling_video(access_key: str, secret_key: str,
                      prompt: str, settings: dict,
                      max_poll_sec: int = 300, poll_interval: float = 5.0) -> list:
    """Kling API: 비디오 생성 submit → poll → video_urls 반환."""
    model_name = settings.get("model", "kling-v1")
    duration = settings.get("duration", "5")
    mode = settings.get("mode", "std")
    aspect_ratio = settings.get("aspectRatio", "16:9")

    endpoint = f"{kling.KLING_BASE}/videos/text2video"
    payload = {
        "model_name": model_name,
        "prompt": prompt,
        "cfg_scale": float(settings.get("cfg_scale", 0.5)),
        "mode": mode,
        "aspect_ratio": aspect_ratio,
        "duration": str(duration),
    }

    status_code, _, j = kling.submit_video(access_key, secret_key, endpoint, payload)
    if not j or status_code not in (200, 201):
        msg = (j or {}).get("message", "") if isinstance(j, dict) else ""
        raise RuntimeError(f"Kling submit 오류 ({status_code}): {msg}")

    data = j.get("data") if isinstance(j, dict) else None
    if not data or not isinstance(data, dict):
        raise RuntimeError("Kling submit 응답에 data가 없습니다.")
    task_id = data.get("task_id")
    if not task_id:
        raise RuntimeError("Kling submit 응답에 task_id가 없습니다.")

    deadline = time.time() + max_poll_sec
    while time.time() < deadline:
        time.sleep(poll_interval)
        _, _, pj = kling.get_task(access_key, secret_key, task_id, task_type="video")
        if not pj or not isinstance(pj, dict):
            continue
        pdata = pj.get("data") if isinstance(pj, dict) else None
        if not pdata:
            continue
        status = str(pdata.get("task_status", "")).lower()
        if status in ("succeed", "completed"):
            works = pdata.get("task_result", {}).get("videos") or []
            urls = [w.get("url") for w in works if w.get("url")]
            if urls:
                return urls
            raise RuntimeError("Kling 완료되었으나 비디오 URL이 비어있습니다.")
        if status in ("failed", "error"):
            err = pdata.get("task_status_msg") or "알 수 없는 오류"
            raise RuntimeError(f"Kling 작업 실패: {err}")

    raise RuntimeError(f"Kling 작업 시간 초과 ({max_poll_sec}초)")


def render_kling_web_tab(cfg: AppConfig, sidebar: SidebarState):
    """Kling 비디오 생성 탭."""
    _init_state(cfg)

    # ── 대기 중인 생성 요청 처리 (2단계: 실제 API 호출) ──
    pending = st.session_state.get("_kling_pending_generate")
    if pending:
        del st.session_state["_kling_pending_generate"]
        try:
            video_urls = call_with_lease(
                cfg,
                test_mode=False,
                provider="kling",
                mock_fn=lambda: [],
                real_fn=lambda kp: _call_kling_video(
                    kp["access_key"], kp["secret_key"],
                    pending["prompt"], pending["settings"],
                ),
            )
        except Exception as e:
            video_urls = []
            st.session_state["_kling_error_msg"] = f"Kling API 오류: {e}"

        # 로딩 아이템 업데이트
        for item in st.session_state.get("kling_web_history", []):
            if item.get("item_id") == pending["item_id"] and item.get("loading"):
                item["video_urls"] = video_urls
                item["loading"] = False
                if _is_authenticated() and video_urls:
                    try:
                        update_kling_web_video_urls(cfg, pending["item_id"], video_urls)
                    except Exception:
                        pass
                break
        st.rerun()

    # ── 에러 메시지 표시 (이전 rerun에서 저장된 것) ──
    _err = st.session_state.pop("_kling_error_msg", None)
    if _err:
        st.toast(_err, icon="⚠️")

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

    history = st.session_state.get("kling_web_history", [])
    result = _kling_component(frame_height=900, history=history)

    if not result or not isinstance(result, dict):
        return

    action = result.get("action")
    ts = result.get("ts", 0)

    # 중복 실행 방지: 처리 완료된 action key set으로 체크
    _item_id = result.get("item_id", "")
    dedup_key = f"{action}_{ts}_{_item_id}"
    _processed = st.session_state.setdefault("_kling_processed_actions", set())
    if dedup_key in _processed:
        return
    _processed.add(dedup_key)
    if len(_processed) > 100:
        st.session_state["_kling_processed_actions"] = {dedup_key}

    if action == "generate":
        # 이미 대기 중인 요청이 있으면 무시 (중복 방지)
        if st.session_state.get("_kling_pending_generate"):
            return

        prompt_text = result.get("prompt", "")
        settings = result.get("settings", {})
        item_id = result.get("item_id")

        if not sidebar.test_mode:
            # Real API → 로딩 아이템 먼저 표시, 다음 rerun에서 API 호출
            new_item = {
                "item_id": item_id,
                "prompt": prompt_text,
                "model_id": result.get("model_id"),
                "model_ver": result.get("model_ver"),
                "model_label": result.get("model_label"),
                "frame_mode": result.get("frame_mode"),
                "sound_enabled": result.get("sound_enabled"),
                "settings": settings,
                "has_start_frame": bool(result.get("start_frame")),
                "has_end_frame": bool(result.get("end_frame")),
                "start_frame_data": result.get("start_frame") or None,
                "end_frame_data": result.get("end_frame") or None,
                "video_urls": [],
                "loading": True,
                "loading_ts": ts,
            }

            if _is_authenticated():
                try:
                    insert_kling_web_item(
                        cfg, st.session_state["user_id"], new_item,
                    )
                except Exception:
                    pass

            st.session_state.setdefault("kling_web_history", []).insert(0, new_item)

            # 다음 rerun에서 처리할 대기 요청 저장
            st.session_state["_kling_pending_generate"] = {
                "item_id": item_id,
                "prompt": prompt_text,
                "settings": settings,
            }
        else:
            # Mock ON → 기존 동작 유지 (JS가 mock 완료 이벤트 전달)
            new_item = {
                "item_id": item_id,
                "prompt": prompt_text,
                "model_id": result.get("model_id"),
                "model_ver": result.get("model_ver"),
                "model_label": result.get("model_label"),
                "frame_mode": result.get("frame_mode"),
                "sound_enabled": result.get("sound_enabled"),
                "settings": settings,
                "has_start_frame": bool(result.get("start_frame")),
                "has_end_frame": bool(result.get("end_frame")),
                "start_frame_data": result.get("start_frame") or None,
                "end_frame_data": result.get("end_frame") or None,
                "video_urls": [],
                "loading": True,
                "loading_ts": ts,
            }

            if _is_authenticated():
                try:
                    insert_kling_web_item(
                        cfg, st.session_state["user_id"], new_item,
                    )
                except Exception:
                    pass

            st.session_state.setdefault("kling_web_history", []).insert(0, new_item)

        st.rerun()

    # ── 로딩 완료 이벤트 ──
    elif action == "loading_complete":
        item_id = result.get("item_id")
        video_urls = result.get("video_urls", [])
        for item in st.session_state.get("kling_web_history", []):
            if item.get("item_id") == item_id:
                item["loading"] = False
                item["video_urls"] = video_urls
                break
        if _is_authenticated() and video_urls:
            try:
                update_kling_web_video_urls(cfg, item_id, video_urls)
            except Exception:
                pass
        st.rerun()


TAB = {
    "tab_id": "kling_web",
    "title": "🎬 Kling",
    "required_features": {"tab.kling_web"},
    "render": render_kling_web_tab,
}
