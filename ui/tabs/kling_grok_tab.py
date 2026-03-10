# ui/tabs/kling_grok_tab.py
"""Kling 비디오 생성 페이지 (Grok Imagine) — declare_component 양방향 통신."""
import time
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from core.config import AppConfig
from core.api_bridge import call_with_lease
from core.db import insert_kling_web_item, load_kling_web_history, update_kling_web_video_urls, load_school_kling_gallery, load_mj_gallery, load_nanobanana_sessions
from providers import grok_video
from ui.sidebar import SidebarState

_COMPONENT_DIR = Path(__file__).resolve().parent / "templates" / "kling_grok"
_kling_component_func = components.declare_component("kling_grok_component", path=str(_COMPONENT_DIR))


def _is_authenticated() -> bool:
    return st.session_state.get("auth_logged_in") and st.session_state.get("user_id", "guest") != "guest"


def _init_state(cfg: AppConfig):
    """세션 상태 초기화: 로그인 사용자는 DB에서 로드."""
    if "kling_grok_history" in st.session_state and st.session_state.get("_grok_db_loaded"):
        return

    if _is_authenticated():
        items = load_kling_web_history(cfg, st.session_state["user_id"])
        if items:
            st.session_state.kling_grok_history = items
            st.session_state["_grok_db_loaded"] = True
            return

    if "kling_grok_history" not in st.session_state:
        st.session_state.kling_grok_history = []
    st.session_state["_grok_db_loaded"] = True


def _kling_component(
    frame_height: int = 900,
    history: list | None = None,
    key: str = "kling_grok_main",
    enabled_features: list | None = None,
    school_gallery: list | None = None,
    source_gallery: list | None = None,
):
    """Kling 커스텀 컴포넌트 래퍼."""
    return _kling_component_func(
        frame_height=frame_height,
        history=history or [],
        enabled_features=enabled_features or [],
        school_gallery=school_gallery,
        source_gallery=source_gallery or [],
        key=key,
        default=None,
    )


def _map_kling_to_grok(settings: dict) -> dict:
    """UI 설정을 Grok Imagine 파라미터로 변환."""
    ratio = settings.get("ratio", "16:9")
    grok_ratios = {"1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3"}
    if ratio not in grok_ratios:
        ratio = "16:9"

    grok_settings: dict = {"aspectRatio": ratio}

    duration = str(settings.get("duration", "8"))
    grok_settings["duration"] = duration

    resolution = settings.get("resolution", "720p")
    if resolution in ("1080p", "4k"):
        resolution = "720p"
    grok_settings["resolution"] = resolution

    return grok_settings


def _call_grok_video(
    api_key: str,
    prompt: str, settings: dict,
    start_frame_data: str = "",
    model: str = "",
) -> list:
    """Kling settings → Grok settings 변환 후 Grok Imagine Video API 호출."""
    grok_settings = _map_kling_to_grok(settings)

    return grok_video.generate_video(
        api_key=api_key,
        prompt=prompt,
        settings=grok_settings,
        start_image_url=start_frame_data,
        model=model,
    )


def _get_tab_features(cfg: AppConfig, prefix: str) -> list:
    school_id = st.session_state.get("school_id", "default")
    return [f for f in cfg.get_enabled_features(school_id) if f.startswith(prefix)]


def render_kling_grok_tab(cfg: AppConfig, sidebar: SidebarState):
    """Kling Grok 비디오 생성 탭."""
    _init_state(cfg)

    # ── 대기 중인 생성 요청 처리 (2단계: 실제 API 호출) ──
    pending = st.session_state.get("_grok_pending_generate")
    if pending:
        del st.session_state["_grok_pending_generate"]
        try:
            video_urls = call_with_lease(
                cfg,
                test_mode=False,
                provider="grok",
                mock_fn=lambda: [],
                real_fn=lambda kp: _call_grok_video(
                    kp["api_key"],
                    pending["prompt"], pending["settings"],
                    start_frame_data=pending.get("start_frame_data", ""),
                    model=cfg.grok_model,
                ),
                lease_ttl_sec=420,
            )
            if video_urls and cfg.gcs_bucket_name and cfg.vertex_sa_json:
                from providers.gcs_storage import upload_media_urls
                video_urls = upload_media_urls(
                    cfg.vertex_sa_json, cfg.gcs_bucket_name, video_urls, prefix="grok",
                )
        except Exception as e:
            video_urls = []
            st.session_state["_grok_error_msg"] = f"Video API 오류: {e}"

        # ── 크레딧 차감 (Phase 2) ──
        if video_urls:
            from core.credits import deduct_after_success, get_feature_cost
            try:
                _pdur = int(pending.get("settings", {}).get("duration", "8"))
                _cost = get_feature_cost(cfg, "grok") * _pdur
                new_bal = deduct_after_success(cfg, _cost, tab_id="grok")
                if new_bal >= 0:
                    st.session_state["_grok_credit_toast"] = new_bal
            except Exception:
                pass

        # 로딩 아이템 업데이트
        for item in st.session_state.get("kling_grok_history", []):
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
    _err = st.session_state.pop("_grok_error_msg", None)
    if _err:
        st.toast(_err, icon="⚠️")

    _cred = st.session_state.pop("_grok_credit_toast", None)
    if _cred is not None:
        st.toast(f"크레딧 차감 완료 (잔여: {_cred})", icon="💰")

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

    # 학교 공유 갤러리 데이터 로드
    school_gallery = None
    if st.session_state.get("_grok_gallery_open"):
        school_id = st.session_state.get("school_id", "default")
        school_gallery = load_school_kling_gallery(cfg, school_id)

    # 갤러리 피커용: MJ + NanoBanana 이미지 로드
    source_gallery = []
    if _is_authenticated():
        try:
            mj_items = load_mj_gallery(cfg, st.session_state["user_id"], limit=20)
            for item in mj_items:
                for url in (item.get("images") or []):
                    source_gallery.append({
                        "source": "mj",
                        "prompt": (item.get("prompt") or "")[:60],
                        "url": url,
                    })
        except Exception:
            pass
        try:
            nb_sessions = load_nanobanana_sessions(cfg, st.session_state["user_id"], limit=20)
            for sess in nb_sessions:
                for turn in (sess.get("turns") or []):
                    prompt = (turn.get("prompt") or "")[:60]
                    for url in (turn.get("image_urls") or []):
                        if url:
                            source_gallery.append({
                                "source": "nanobanana",
                                "prompt": prompt,
                                "url": url,
                            })
        except Exception:
            pass

    history = st.session_state.get("kling_grok_history", [])
    result = _kling_component(frame_height=900, history=history,
                              enabled_features=_get_tab_features(cfg, "kling."),
                              school_gallery=school_gallery,
                              source_gallery=source_gallery)

    if not result or not isinstance(result, dict):
        return

    action = result.get("action")
    ts = result.get("ts", 0)

    # 중복 실행 방지
    _item_id = result.get("item_id", "")
    dedup_key = f"{action}_{ts}_{_item_id}"
    _processed = st.session_state.setdefault("_grok_processed_actions", set())
    if dedup_key in _processed:
        return
    _processed.add(dedup_key)
    if len(_processed) > 100:
        st.session_state["_grok_processed_actions"] = {dedup_key}

    if action == "open_gallery":
        st.session_state["_grok_gallery_open"] = True
        st.rerun()
    elif action == "close_gallery":
        st.session_state["_grok_gallery_open"] = False
        st.rerun()
    elif action == "generate":
        if st.session_state.get("_grok_pending_generate"):
            return

        prompt_text = result.get("prompt", "")
        settings = result.get("settings", {})

        # ── 크레딧 확인 (Phase 1) ──
        _dur = int(settings.get("duration", "8"))
        from core.credits import check_credits, get_feature_cost
        _cost = get_feature_cost(cfg, "grok") * _dur
        ok, msg = check_credits(cfg, _cost)
        if not ok:
            st.session_state["_grok_error_msg"] = msg
            st.rerun()
            return
        item_id = result.get("item_id")

        # Grok는 end_frame(interpolation) 미지원 — start_frame만 사용
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
            "has_end_frame": False,
            "start_frame_data": result.get("start_frame") or None,
            "end_frame_data": None,
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

        st.session_state.setdefault("kling_grok_history", []).insert(0, new_item)

        if not sidebar.test_mode:
            st.session_state["_grok_pending_generate"] = {
                "item_id": item_id,
                "prompt": prompt_text,
                "settings": settings,
                "start_frame_data": result.get("start_frame") or "",
            }

        st.rerun()

    elif action == "loading_complete":
        item_id = result.get("item_id")
        video_urls = result.get("video_urls", [])
        for item in st.session_state.get("kling_grok_history", []):
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
    "tab_id": "kling_grok",
    "title": "🎬 Kling",
    "required_features": {"tab.kling_grok"},
    "render": render_kling_grok_tab,
}
