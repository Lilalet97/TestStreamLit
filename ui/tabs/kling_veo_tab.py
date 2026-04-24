# ui/tabs/kling_veo_tab.py
"""Kling 비디오 생성 페이지 (Google Veo) — declare_component 양방향 통신."""
import time
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from core.config import AppConfig
from core.api_bridge import call_with_lease
from core.db import insert_kling_web_item, load_kling_web_history, update_kling_web_video_urls, load_school_kling_gallery, load_mj_gallery, load_nanobanana_sessions
from providers import kling  # noqa: F401 — 향후 Kling 재전환용 보존
from providers import google_veo
from ui.sidebar import SidebarState

_COMPONENT_DIR = Path(__file__).resolve().parent / "templates" / "kling"
_kling_component_func = components.declare_component("kling_veo_component", path=str(_COMPONENT_DIR))


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


def _call_kling_video(access_key: str, secret_key: str,
                      prompt: str, settings: dict,
                      max_poll_sec: int = 300, poll_interval: float = 5.0) -> list:
    """Kling API: 비디오 생성 submit → poll → video_urls 반환."""
    model_name = settings.get("model", "kling-v2.6-std")
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


def _map_kling_to_veo(settings: dict) -> dict:
    """UI 설정을 Veo 파라미터로 변환."""
    ratio = settings.get("ratio", "16:9")
    # Veo는 16:9, 9:16만 지원 → 1:1은 16:9로 fallback
    if ratio not in ("16:9", "9:16"):
        ratio = "16:9"

    veo_settings: dict = {"aspectRatio": ratio}

    # duration: UI에서 직접 Veo 값(5/6/8) 전달
    duration = str(settings.get("duration", "8"))
    veo_settings["durationSeconds"] = duration

    # resolution
    resolution = settings.get("resolution")
    if resolution in ("1080p", "4k"):
        veo_settings["resolution"] = resolution
    elif resolution == "720p":
        veo_settings["resolution"] = resolution

    return veo_settings


def _call_veo_video(
    api_key: str,
    prompt: str, settings: dict,
    start_frame_data: str = "",
    end_frame_data: str = "",
    model: str = "",
    # [VERTEX AI] sa_json: str = "", project_id: str = "", location: str = "",
) -> list:
    """Kling settings → Veo settings 변환 후 Google Veo API 호출."""
    veo_settings = _map_kling_to_veo(settings)
    return google_veo.generate_video(
        api_key=api_key,
        prompt=prompt, settings=veo_settings,
        start_image_data_url=start_frame_data,
        end_image_data_url=end_frame_data,
        model=model or google_veo.DEFAULT_MODEL,
        # [VERTEX AI] sa_json=sa_json, project_id=project_id, location=location,
    )


def _get_tab_features(cfg: AppConfig, prefix: str) -> list:
    school_id = st.session_state.get("school_id", "default")
    return [f for f in cfg.get_enabled_features(school_id) if f.startswith(prefix)]


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
                provider="google_veo",
                mock_fn=lambda: [],
                real_fn=lambda kp: _call_veo_video(
                    kp["api_key"],
                    pending["prompt"], pending["settings"],
                    start_frame_data=pending.get("start_frame_data", ""),
                    end_frame_data=pending.get("end_frame_data", ""),
                    model=cfg.google_veo_model,
                    # [VERTEX AI] kp["sa_json"], kp["project_id"], kp["location"],
                ),
                lease_ttl_sec=420,
                model=cfg.google_veo_model,
            )
            # GCS 업로드 (설정 시)
            if video_urls and cfg.gcs_bucket_name and cfg.vertex_sa_json:
                from providers.gcs_storage import upload_media_urls
                video_urls = upload_media_urls(
                    cfg.vertex_sa_json, cfg.gcs_bucket_name, video_urls, prefix="kling",
                )
        except Exception as e:
            video_urls = []
            st.session_state["_kling_error_msg"] = f"Video API 오류: {e}"

        # ── 크레딧 차감 (Phase 2) ──
        if video_urls:
            from core.credits import deduct_after_success, get_feature_cost
            try:
                _pdur = max(1, min(int(pending.get("settings", {}).get("duration", "8")), 10))
                _cost = get_feature_cost(cfg, "veo") * _pdur
                new_bal = deduct_after_success(cfg, _cost, tab_id="veo")
                if new_bal >= 0:
                    st.session_state["_kling_credit_toast"] = new_bal
            except Exception:
                pass

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
        from core.db import insert_error_log
        insert_error_log(cfg, st.session_state.get("user_id", ""), st.session_state.get("school_id", "default"), "kling_veo", _err)

    _cred = st.session_state.pop("_kling_credit_toast", None)
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
    if st.session_state.get("_kling_gallery_open"):
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

    history = st.session_state.get("kling_web_history", [])
    result = _kling_component(frame_height=900, history=history,
                              enabled_features=_get_tab_features(cfg, "kling."),
                              school_gallery=school_gallery,
                              source_gallery=source_gallery)

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
    if len(_processed) > 500:
        st.session_state["_kling_processed_actions"] = {dedup_key}

    if action == "open_gallery":
        st.session_state["_kling_gallery_open"] = True
        st.rerun()
    elif action == "close_gallery":
        st.session_state["_kling_gallery_open"] = False
        st.rerun()
    elif action == "generate":
        if not _is_authenticated():
            return
        # 이미 대기 중인 요청이 있으면 무시 (중복 방지)
        if st.session_state.get("_kling_pending_generate"):
            return

        prompt_text = result.get("prompt", "")
        if len(prompt_text) > 10000:
            prompt_text = prompt_text[:10000]
        settings = result.get("settings", {})

        # ── 크레딧 확인 (Phase 1) ──
        _dur = max(1, min(int(settings.get("duration", "8")), 10))
        from core.credits import check_credits, get_feature_cost
        _cost = get_feature_cost(cfg, "veo") * _dur
        ok, msg = check_credits(cfg, _cost)
        if not ok:
            st.session_state["_kling_error_msg"] = msg
            st.rerun()
            return
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
                "start_frame_data": result.get("start_frame") or "",
                "end_frame_data": result.get("end_frame") or "",
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
                "loading": False,
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
    "tab_id": "kling_veo",
    "title": "Video Create(ex. Kling)",
    "required_features": {"tab.kling_veo"},
    "render": render_kling_web_tab,
}
