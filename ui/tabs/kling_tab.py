# ui/tabs/kling_tab.py
"""Kling 비디오 생성 페이지 (Kling API) — declare_component 양방향 통신."""
import time
import threading
import logging
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from core.config import AppConfig
from core.api_bridge import call_with_lease
from core.db import (
    insert_kling_web_item, load_kling_web_history, update_kling_web_video_urls,
    update_kling_task_id, load_kling_pending_tasks,
    load_school_kling_gallery, load_mj_gallery, load_nanobanana_sessions,
)
from providers import kling
from ui.sidebar import SidebarState

_log = logging.getLogger(__name__)

# ── 백그라운드 폴링 레지스트리 (서버 프로세스 전역) ──
_polling_tasks: dict[str, threading.Thread] = {}
_polling_lock = threading.Lock()

_COMPONENT_DIR = Path(__file__).resolve().parent / "templates" / "kling"
_kling_component_func = components.declare_component("kling_component", path=str(_COMPONENT_DIR))


def _is_authenticated() -> bool:
    return st.session_state.get("auth_logged_in") and st.session_state.get("user_id", "guest") != "guest"


def _init_state(cfg: AppConfig):
    """세션 상태 초기화: 로그인 사용자는 DB에서 로드."""
    # 폴링 중인 항목이 있으면 DB 강제 재로드 (완료 반영)
    with _polling_lock:
        has_active = bool(_polling_tasks)
    if has_active:
        st.session_state.pop("_klingapi_db_loaded", None)

    if "klingapi_history" in st.session_state and st.session_state.get("_klingapi_db_loaded"):
        return

    if _is_authenticated():
        items = load_kling_web_history(cfg, st.session_state["user_id"])
        if items:
            # 백그라운드 폴링 중인 항목은 loading 표시
            with _polling_lock:
                active = set(_polling_tasks.keys())
            for item in items:
                if item["item_id"] in active or (not item["video_urls"] and item.get("task_id")):
                    item["loading"] = True
            st.session_state.klingapi_history = items
            st.session_state["_klingapi_db_loaded"] = True
            return

    if "klingapi_history" not in st.session_state:
        st.session_state.klingapi_history = []
    st.session_state["_klingapi_db_loaded"] = True


def _kling_component(
    frame_height: int = 900,
    history: list | None = None,
    key: str = "klingapi_main",
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


def _submit_kling_video(access_key: str, secret_key: str,
                        prompt: str, settings: dict,
                        start_frame_data: str = "",
                        end_frame_data: str = "",
                        model: str = "") -> tuple[str, str]:
    """Kling API: 비디오 생성 submit만 수행. (task_id, task_type) 반환."""
    model_name = model
    duration = settings.get("duration", "5")
    mode = settings.get("mode", "std")
    aspect_ratio = settings.get("aspectRatio", "16:9")

    is_i2v = bool(start_frame_data)
    if is_i2v:
        endpoint = f"{kling.KLING_BASE}/videos/image2video"
        payload = {
            "model_name": model_name, "prompt": prompt,
            "image": start_frame_data,
            "cfg_scale": float(settings.get("cfg_scale", 0.5)),
            "mode": mode, "aspect_ratio": aspect_ratio,
            "duration": str(duration),
        }
        if end_frame_data:
            payload["image_tail"] = end_frame_data
    else:
        endpoint = f"{kling.KLING_BASE}/videos/text2video"
        payload = {
            "model_name": model_name, "prompt": prompt,
            "cfg_scale": float(settings.get("cfg_scale", 0.5)),
            "mode": mode, "aspect_ratio": aspect_ratio,
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

    task_type = "image2video" if is_i2v else "video"
    return task_id, task_type


def _poll_kling_task(access_key: str, secret_key: str,
                     task_id: str, task_type: str,
                     max_poll_sec: int = 300, poll_interval: float = 5.0) -> list:
    """Kling API: task_id로 폴링하여 video_urls 반환."""
    deadline = time.time() + max_poll_sec
    while time.time() < deadline:
        time.sleep(poll_interval)
        _, _, pj = kling.get_task(access_key, secret_key, task_id, task_type=task_type)
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


def _bg_poll_and_save(cfg: AppConfig, access_key: str, secret_key: str,
                      task_id: str, task_type: str, item_id: str,
                      settings: dict, user_id: str = "", school_id: str = ""):
    """백그라운드 스레드: 폴링 → DB 저장. 세션 독립적."""
    try:
        video_urls = _poll_kling_task(access_key, secret_key, task_id, task_type)
        # GCS 업로드
        if video_urls and cfg.gcs_bucket_name and cfg.vertex_sa_json:
            try:
                from providers.gcs_storage import upload_media_urls
                video_urls = upload_media_urls(
                    cfg.vertex_sa_json, cfg.gcs_bucket_name, video_urls, prefix="kling",
                )
            except Exception:
                pass
        update_kling_web_video_urls(cfg, item_id, video_urls)
        # 크레딧 차감 (세션 독립 — DB 직접 호출)
        if video_urls and user_id:
            try:
                from core.credits import get_feature_cost
                from core.db import deduct_user_balance
                _dur = max(1, min(int(settings.get("duration", "5")), 10))
                _cost = get_feature_cost(cfg, "kling") * _dur
                deduct_user_balance(cfg, user_id, _cost, tab_id="kling", school_id=school_id)
            except Exception:
                _log.warning("Kling bg credit deduct failed: %s", item_id)
        _log.info("Kling bg poll done: %s → %d urls", item_id, len(video_urls))
    except Exception as e:
        _log.warning("Kling bg poll failed: %s → %s", item_id, e)
        # 실패해도 빈 배열로 업데이트하지 않음 — 다음 접속 시 재시도 가능
    finally:
        with _polling_lock:
            _polling_tasks.pop(item_id, None)


def _start_bg_poll(cfg: AppConfig, access_key: str, secret_key: str,
                   task_id: str, task_type: str, item_id: str,
                   settings: dict, user_id: str = "", school_id: str = ""):
    """백그라운드 폴링 스레드 시작 (중복 방지)."""
    with _polling_lock:
        if item_id in _polling_tasks and _polling_tasks[item_id].is_alive():
            return  # 이미 폴링 중
        t = threading.Thread(
            target=_bg_poll_and_save,
            args=(cfg, access_key, secret_key, task_id, task_type, item_id, settings,
                  user_id, school_id),
            daemon=True,
        )
        _polling_tasks[item_id] = t
        t.start()


def _get_tab_features(cfg: AppConfig, prefix: str) -> list:
    school_id = st.session_state.get("school_id", "default")
    return [f for f in cfg.get_enabled_features(school_id) if f.startswith(prefix)]


def render_kling_tab(cfg: AppConfig, sidebar: SidebarState):
    """Kling 비디오 생성 탭 (Kling API)."""

    # ── 미완료 task 자동 복구 (_init_state 전에 실행해야 loading 상태 반영) ──
    if not st.session_state.get("_klingapi_recovery_done"):
        st.session_state["_klingapi_recovery_done"] = True
        try:
            pending_tasks = load_kling_pending_tasks(cfg)
            if pending_tasks:
                _recovery_keys = {}

                def _get_keys(kp):
                    _recovery_keys.update(kp)
                    return True

                call_with_lease(
                    cfg, test_mode=False, provider="kling",
                    mock_fn=lambda: True,
                    real_fn=_get_keys,
                    lease_ttl_sec=10,
                )
                if _recovery_keys:
                    for task in pending_tasks:
                        _start_bg_poll(
                            cfg, _recovery_keys["access_key"], _recovery_keys["secret_key"],
                            task["task_id"], task["task_type"] or "video",
                            task["item_id"], task["settings"],
                            user_id=task.get("user_id", ""),
                            school_id=task.get("school_id", ""),
                        )
                    _log.info("Kling recovery: %d pending tasks resumed", len(pending_tasks))
        except Exception:
            pass

    _init_state(cfg)

    # ── 대기 중인 생성 요청 처리 (submit → 백그라운드 폴링) ──
    pending = st.session_state.get("_klingapi_pending_generate")
    if pending:
        del st.session_state["_klingapi_pending_generate"]
        _captured_keys = {}

        def _submit_and_capture(kp):
            _captured_keys.update(kp)
            return _submit_kling_video(
                kp["access_key"], kp["secret_key"],
                pending["prompt"], pending["settings"],
                start_frame_data=pending.get("start_frame_data", ""),
                end_frame_data=pending.get("end_frame_data", ""),
                model=cfg.kling_model,
            )

        try:
            task_id, task_type = call_with_lease(
                cfg, test_mode=False, provider="kling",
                mock_fn=lambda: ("mock_task", "video"),
                real_fn=_submit_and_capture,
                lease_ttl_sec=60,
            )
            if _is_authenticated() and task_id:
                try:
                    update_kling_task_id(cfg, pending["item_id"], task_id, task_type)
                except Exception:
                    pass

            if task_id and not task_id.startswith("mock_") and _captured_keys:
                _start_bg_poll(
                    cfg, _captured_keys["access_key"], _captured_keys["secret_key"],
                    task_id, task_type, pending["item_id"],
                    pending["settings"],
                    user_id=st.session_state.get("user_id", ""),
                    school_id=st.session_state.get("school_id", ""),
                )

        except Exception as e:
            st.session_state["_klingapi_error_msg"] = f"Video API 오류: {e}"
            # submit 실패 시 로딩 상태 해제
            for item in st.session_state.get("klingapi_history", []):
                if item.get("item_id") == pending["item_id"] and item.get("loading"):
                    item["loading"] = False
                    break

        st.rerun()

    # ── 에러 메시지 표시 (이전 rerun에서 저장된 것) ──
    _err = st.session_state.pop("_klingapi_error_msg", None)
    if _err:
        st.toast(_err, icon="⚠️")
        from core.db import insert_error_log
        insert_error_log(cfg, st.session_state.get("user_id", ""), st.session_state.get("school_id", "default"), "kling", _err)

    _cred = st.session_state.pop("_klingapi_credit_toast", None)
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
    if st.session_state.get("_klingapi_gallery_open"):
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

    history = st.session_state.get("klingapi_history", [])
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
    _processed = st.session_state.setdefault("_klingapi_processed_actions", set())
    if dedup_key in _processed:
        return
    _processed.add(dedup_key)
    if len(_processed) > 500:
        st.session_state["_klingapi_processed_actions"] = {dedup_key}

    if action == "open_gallery":
        st.session_state["_klingapi_gallery_open"] = True
        st.rerun()
    elif action == "close_gallery":
        st.session_state["_klingapi_gallery_open"] = False
        st.rerun()
    elif action == "generate":
        if not _is_authenticated():
            return
        # 이미 대기 중인 요청이 있으면 무시 (중복 방지)
        if st.session_state.get("_klingapi_pending_generate"):
            return

        prompt_text = result.get("prompt", "")
        if len(prompt_text) > 10000:
            prompt_text = prompt_text[:10000]
        settings = result.get("settings", {})

        # ── 크레딧 확인 (Phase 1) ──
        _dur = max(1, min(int(settings.get("duration", "5")), 10))
        from core.credits import check_credits, get_feature_cost
        _cost = get_feature_cost(cfg, "kling") * _dur
        ok, msg = check_credits(cfg, _cost)
        if not ok:
            st.session_state["_klingapi_error_msg"] = msg
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

            st.session_state.setdefault("klingapi_history", []).insert(0, new_item)

            # 다음 rerun에서 처리할 대기 요청 저장
            st.session_state["_klingapi_pending_generate"] = {
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

            st.session_state.setdefault("klingapi_history", []).insert(0, new_item)

        st.rerun()

    # ── 로딩 완료 이벤트 ──
    elif action == "loading_complete":
        item_id = result.get("item_id")
        video_urls = result.get("video_urls", [])
        for item in st.session_state.get("klingapi_history", []):
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
    "tab_id": "kling",
    "title": "Video Create(ex. Kling)",
    "required_features": {"tab.kling"},
    "render": render_kling_tab,
}
