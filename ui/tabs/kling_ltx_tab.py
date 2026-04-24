# ui/tabs/kling_ltx_tab.py
"""Kling 비디오 생성 페이지 (LTX Video) — declare_component 양방향 통신."""
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from core.config import AppConfig
from core.api_bridge import call_with_lease
from core.db import insert_kling_web_item, load_kling_web_history, update_kling_web_video_urls, load_school_kling_gallery, load_mj_gallery, load_nanobanana_sessions
from providers import ltx_video
from ui.sidebar import SidebarState

# LTX 전용 HTML (end frame 숨김)
_COMPONENT_DIR = Path(__file__).resolve().parent / "templates" / "kling_ltx"
_kling_component_func = components.declare_component("kling_ltx_component", path=str(_COMPONENT_DIR))

# ── 세션 키 (다른 Kling 탭과 분리) ──
_PREFIX = "ltx"
_K_HISTORY = "kling_web_history"
_K_DB_LOADED = "_kling_db_loaded"
_K_PROCESSED = f"_{_PREFIX}_processed_actions"
_K_PENDING = f"_{_PREFIX}_pending_generate"
_K_ERROR = f"_{_PREFIX}_error_msg"
_K_CREDIT = f"_{_PREFIX}_credit_toast"
_K_GALLERY_OPEN = f"_{_PREFIX}_gallery_open"


def _is_authenticated() -> bool:
    return (
        st.session_state.get("auth_logged_in")
        and st.session_state.get("user_id", "guest") != "guest"
    )


def _kling_component(frame_height=900, history=None, key="kling_ltx_main",
                     enabled_features=None, school_gallery=None, source_gallery=None):
    return _kling_component_func(
        history=history or [],
        frame_height=frame_height,
        enabled_features=enabled_features or [],
        school_gallery=school_gallery,
        source_gallery=source_gallery or [],
        key=key,
        default=None,
    )


def _init_state(cfg):
    if not st.session_state.get(_K_DB_LOADED):
        if _is_authenticated():
            try:
                items = load_kling_web_history(cfg, st.session_state["user_id"])
                st.session_state[_K_HISTORY] = items
            except Exception:
                st.session_state[_K_HISTORY] = []
        else:
            st.session_state[_K_HISTORY] = []
        st.session_state[_K_DB_LOADED] = True


def _map_kling_to_ltx(settings: dict) -> dict:
    """UI 설정을 LTX 파라미터로 변환."""
    ratio = settings.get("ratio", "16:9")
    # LTX는 1920x1080 (16:9) 또는 1080x1920 (9:16)
    if ratio == "9:16":
        resolution = "1080x1920"
    else:
        resolution = "1920x1080"

    duration = max(1, min(int(settings.get("duration", "5")), 15))

    return {
        "resolution": resolution,
        "duration": duration,
    }


def _call_ltx_video(
    api_key: str,
    prompt: str, settings: dict,
    start_frame_data: str = "",
    model: str = "ltx-2-3-fast",
) -> list:
    """Kling settings → LTX settings 변환 후 LTX Video API 호출."""
    ltx_settings = _map_kling_to_ltx(settings)

    if start_frame_data and start_frame_data.startswith("data:"):
        # image-to-video: 이미지를 GCS에 업로드하여 URL 획득
        from core.config import load_config
        cfg = load_config()
        if cfg.gcs_bucket_name and cfg.vertex_sa_json:
            from providers.gcs_storage import upload_single_media_url
            image_url = upload_single_media_url(
                cfg.vertex_sa_json, cfg.gcs_bucket_name,
                start_frame_data, prefix="ltx/frames",
            )
        else:
            image_url = start_frame_data

        data_url = ltx_video.image_to_video(
            api_key=api_key,
            prompt=prompt,
            image_url=image_url,
            model=model,
            duration=ltx_settings["duration"],
            resolution=ltx_settings["resolution"],
        )
    else:
        # text-to-video
        data_url = ltx_video.text_to_video(
            api_key=api_key,
            prompt=prompt,
            model=model,
            duration=ltx_settings["duration"],
            resolution=ltx_settings["resolution"],
        )

    return [data_url] if data_url else []


def _get_tab_features(cfg: AppConfig, prefix: str) -> list:
    school_id = st.session_state.get("school_id", "default")
    return [f for f in cfg.get_enabled_features(school_id) if f.startswith(prefix)]


def render_kling_ltx_tab(cfg: AppConfig, sidebar: SidebarState):
    """LTX Video 탭."""
    _init_state(cfg)

    # ── 대기 중인 생성 요청 처리 ──
    pending = st.session_state.get(_K_PENDING)
    if pending:
        del st.session_state[_K_PENDING]
        try:
            video_urls = call_with_lease(
                cfg,
                test_mode=sidebar.test_mode,
                provider="ltx_video",
                mock_fn=lambda: [],
                real_fn=lambda kp: _call_ltx_video(
                    kp["api_key"],
                    pending["prompt"], pending["settings"],
                    start_frame_data=pending.get("start_frame_data", ""),
                    model="ltx-2-3-fast",
                ),
                lease_ttl_sec=420,
                model="ltx-2-3-fast",
            )
            # GCS 업로드
            if video_urls and cfg.gcs_bucket_name and cfg.vertex_sa_json:
                from providers.gcs_storage import upload_media_urls
                video_urls = upload_media_urls(
                    cfg.vertex_sa_json, cfg.gcs_bucket_name, video_urls, prefix="ltx",
                )
        except Exception as e:
            video_urls = []
            st.session_state[_K_ERROR] = f"LTX Video 오류: {e}"

        # ── 크레딧 차감 ──
        if video_urls:
            from core.credits import deduct_after_success, get_feature_cost
            try:
                _pdur = max(1, min(int(pending.get("settings", {}).get("duration", "5")), 15))
                _cost = get_feature_cost(cfg, "ltx") * _pdur
                new_bal = deduct_after_success(cfg, _cost, tab_id="ltx")
                if new_bal >= 0:
                    st.session_state[_K_CREDIT] = new_bal
            except Exception:
                pass

        # 로딩 아이템 업데이트
        for item in st.session_state.get(_K_HISTORY, []):
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

    # ── 에러/크레딧 메시지 ──
    _err = st.session_state.pop(_K_ERROR, None)
    if _err:
        st.toast(_err, icon="⚠️")
        from core.db import insert_error_log
        insert_error_log(cfg, st.session_state.get("user_id", ""), st.session_state.get("school_id", "default"), "kling_ltx", _err)

    _cred = st.session_state.pop(_K_CREDIT, None)
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

    # 학교 공유 갤러리
    school_gallery = None
    if st.session_state.get(_K_GALLERY_OPEN):
        school_id = st.session_state.get("school_id", "default")
        school_gallery = load_school_kling_gallery(cfg, school_id)

    # 갤러리 피커: MJ + NB 이미지
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
            nb_sessions = load_nanobanana_sessions(cfg, st.session_state["user_id"], limit=20, tab_id=None)
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

    history = st.session_state.get(_K_HISTORY, [])
    result = _kling_component(frame_height=900, history=history,
                              enabled_features=_get_tab_features(cfg, "kling."),
                              school_gallery=school_gallery,
                              source_gallery=source_gallery)

    if not result or not isinstance(result, dict):
        return

    action = result.get("action")
    ts = result.get("ts", 0)
    _item_id = result.get("item_id", "")
    dedup_key = f"{action}_{ts}_{_item_id}"
    _processed = st.session_state.setdefault(_K_PROCESSED, set())
    if dedup_key in _processed:
        return
    _processed.add(dedup_key)
    if len(_processed) > 500:
        st.session_state[_K_PROCESSED] = {dedup_key}

    if action == "open_gallery":
        st.session_state[_K_GALLERY_OPEN] = True
        st.rerun()
    elif action == "close_gallery":
        st.session_state[_K_GALLERY_OPEN] = False
        st.rerun()
    elif action == "generate":
        if not _is_authenticated():
            return
        if st.session_state.get(_K_PENDING):
            return

        prompt_text = result.get("prompt", "")
        if len(prompt_text) > 10000:
            prompt_text = prompt_text[:10000]
        settings = result.get("settings", {})

        # ── 크레딧 확인 (Phase 1) ──
        _dur = max(1, min(int(settings.get("duration", "5")), 15))
        from core.credits import check_credits, get_feature_cost
        _cost = get_feature_cost(cfg, "ltx") * _dur
        ok, msg = check_credits(cfg, _cost)
        if not ok:
            st.session_state[_K_ERROR] = msg
            st.rerun()
            return
        item_id = result.get("item_id")

        if not sidebar.test_mode:
            new_item = {
                "item_id": item_id,
                "prompt": prompt_text,
                "model_id": "ltx-2-3-fast",
                "model_ver": "2.3",
                "model_label": "LTX Video",
                "frame_mode": result.get("frame_mode"),
                "sound_enabled": False,
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
                    insert_kling_web_item(cfg, st.session_state["user_id"], new_item)
                except Exception:
                    pass

            st.session_state.setdefault(_K_HISTORY, []).insert(0, new_item)

            st.session_state[_K_PENDING] = {
                "item_id": item_id,
                "prompt": prompt_text,
                "settings": settings,
                "start_frame_data": result.get("start_frame") or "",
            }
        else:
            new_item = {
                "item_id": item_id,
                "prompt": prompt_text,
                "model_id": "ltx-2-3-fast",
                "model_ver": "2.3",
                "model_label": "LTX Video",
                "frame_mode": result.get("frame_mode"),
                "sound_enabled": False,
                "settings": settings,
                "has_start_frame": bool(result.get("start_frame")),
                "has_end_frame": False,
                "start_frame_data": result.get("start_frame") or None,
                "end_frame_data": None,
                "video_urls": [],
                "loading": False,
                "loading_ts": ts,
            }
            if _is_authenticated():
                try:
                    insert_kling_web_item(cfg, st.session_state["user_id"], new_item)
                except Exception:
                    pass
            st.session_state.setdefault(_K_HISTORY, []).insert(0, new_item)

        st.rerun()

    elif action == "loading_complete":
        item_id = result.get("item_id")
        video_urls = result.get("video_urls", [])
        for item in st.session_state.get(_K_HISTORY, []):
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
    "tab_id": "kling_ltx",
    "title": "Video Create",
    "required_features": {"tab.kling_ltx"},
    "render": render_kling_ltx_tab,
}
