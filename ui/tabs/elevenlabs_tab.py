# ui/tabs/elevenlabs_tab.py
"""ElevenLabs 페이지 — TTS, VTV, SFX, Voice Clone."""
import base64
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from core.config import AppConfig
from core.api_bridge import call_with_lease
from core.db import insert_elevenlabs_item, load_elevenlabs_history, update_elevenlabs_audio_url, load_school_elevenlabs_gallery
from providers import elevenlabs
from ui.sidebar import SidebarState

_COMPONENT_DIR = Path(__file__).resolve().parent / "templates" / "elevenlabs"
_elevenlabs_component_func = components.declare_component("elevenlabs_component", path=str(_COMPONENT_DIR))


def _is_authenticated() -> bool:
    return st.session_state.get("auth_logged_in") and st.session_state.get("user_id", "guest") != "guest"


def _init_state(cfg: AppConfig):
    """세션 상태 초기화: 로그인 사용자는 DB에서 로드."""
    if "elevenlabs_history" in st.session_state and st.session_state.get("_elevenlabs_db_loaded"):
        return

    if _is_authenticated():
        items = load_elevenlabs_history(cfg, st.session_state["user_id"])
        if items:
            st.session_state.elevenlabs_history = items
            st.session_state["_elevenlabs_db_loaded"] = True
            return

    if "elevenlabs_history" not in st.session_state:
        st.session_state.elevenlabs_history = []
    st.session_state["_elevenlabs_db_loaded"] = True


def _elevenlabs_component(
    frame_height: int = 900,
    history: list | None = None,
    key: str = "elevenlabs_main",
    enabled_features: list | None = None,
    school_gallery: list | None = None,
    default_model: str = "",
    clone_result: dict | None = None,
    cloned_voices: list | None = None,
):
    """ElevenLabs 커스텀 컴포넌트 래퍼."""
    return _elevenlabs_component_func(
        frame_height=frame_height,
        history=history or [],
        enabled_features=enabled_features or [],
        school_gallery=school_gallery,
        default_model=default_model,
        clone_result=clone_result,
        cloned_voices=cloned_voices or [],
        key=key,
        default=None,
    )


def _get_tab_features(cfg: AppConfig, prefix: str) -> list:
    school_id = st.session_state.get("school_id", "default")
    return [f for f in cfg.get_enabled_features(school_id) if f.startswith(prefix)]


def render_elevenlabs_tab(cfg: AppConfig, sidebar: SidebarState):
    """ElevenLabs TTS 탭."""
    _init_state(cfg)

    # ── 대기 중인 생성 요청 처리 (2단계: 실제 API 호출) ──
    pending = st.session_state.get("_el_pending_generate")
    if pending:
        del st.session_state["_el_pending_generate"]
        pending_action = pending.get("pending_action", "tts")
        audio_url = None
        clone_result = None

        try:
            if pending_action == "vtv":
                audio_bytes = base64.b64decode(pending["audio_b64"])
                audio_url = call_with_lease(
                    cfg, test_mode=False, provider="elevenlabs",
                    mock_fn=lambda: None,
                    real_fn=lambda kp: elevenlabs.voice_to_voice(
                        api_key=kp["api_key"],
                        voice_id=pending["voice_id"],
                        audio_bytes=audio_bytes,
                        model_id=cfg.elevenlabs_vtv_model,
                        stability=float(pending["settings"].get("stability", 0.5)),
                        similarity_boost=float(pending["settings"].get("similarity", 0.75)),
                        style=float(pending["settings"].get("style_exaggeration", 0.0)),
                        use_speaker_boost=pending.get("speaker_boost", True),
                    ),
                )
            elif pending_action == "sfx":
                audio_url = call_with_lease(
                    cfg, test_mode=False, provider="elevenlabs",
                    mock_fn=lambda: None,
                    real_fn=lambda kp: elevenlabs.sound_generation(
                        api_key=kp["api_key"],
                        text=pending["text"],
                        duration_seconds=pending.get("duration_seconds"),
                        prompt_influence=float(pending.get("prompt_influence", 0.3)),
                    ),
                )
            elif pending_action == "clone":
                audio_bytes = base64.b64decode(pending["audio_b64"])
                clone_result = call_with_lease(
                    cfg, test_mode=False, provider="elevenlabs",
                    mock_fn=lambda: None,
                    real_fn=lambda kp: elevenlabs.voice_clone(
                        api_key=kp["api_key"],
                        name=pending.get("clone_name", "My Voice"),
                        audio_bytes=audio_bytes,
                    ),
                )
            else:
                # TTS (기본)
                audio_url = call_with_lease(
                    cfg, test_mode=False, provider="elevenlabs",
                    mock_fn=lambda: None,
                    real_fn=lambda kp: elevenlabs.text_to_speech(
                        api_key=kp["api_key"],
                        voice_id=pending["voice_id"],
                        text=pending["text"],
                        model_id=cfg.elevenlabs_model,
                        stability=float(pending["settings"].get("stability", 0.5)),
                        similarity_boost=float(pending["settings"].get("similarity", 0.75)),
                        style=float(pending["settings"].get("style_exaggeration", 0.0)),
                        use_speaker_boost=pending.get("speaker_boost", True),
                    ),
                )

        except Exception as e:
            audio_url = None
            clone_result = None
            err_str = str(e)
            if "missing_permissions" in err_str or "missing the permission" in err_str:
                st.session_state["_el_error_msg"] = (
                    "이 API 키에 Voice Clone 권한이 없습니다. "
                    "ElevenLabs Creator 플랜 이상이 필요합니다."
                )
            else:
                st.session_state["_el_error_msg"] = f"ElevenLabs API 오류: {e}"

        # clone 결과는 history가 아닌 별도 세션으로 전달
        if pending_action == "clone":
            if clone_result:
                st.session_state["_el_clone_result"] = clone_result
                st.session_state["_el_cloned_voices"] = None  # 캐시 갱신
                # ── clone 크레딧 차감 (Phase 2) ──
                from core.credits import deduct_after_success, get_feature_cost
                try:
                    _cost = get_feature_cost(cfg, "el_clone")
                    new_bal = deduct_after_success(cfg, _cost, tab_id="el_clone")
                    if new_bal >= 0:
                        st.session_state["_el_credit_toast"] = new_bal
                except Exception:
                    pass
            else:
                st.session_state["_el_error_msg"] = st.session_state.get(
                    "_el_error_msg", "Voice clone에 실패했습니다."
                )
            st.rerun()

        # ── 크레딧 차감 (Phase 2) ──
        if audio_url:
            from core.credits import deduct_after_success, get_feature_cost
            _fid_map = {"tts": "el_tts", "vtv": "el_vtv", "sfx": "el_sfx"}
            _fid = _fid_map.get(pending_action, "el_tts")
            try:
                _cost = get_feature_cost(cfg, _fid)
                new_bal = deduct_after_success(cfg, _cost, tab_id=_fid)
                if new_bal >= 0:
                    st.session_state["_el_credit_toast"] = new_bal
            except Exception:
                pass

        # GCS 업로드
        if audio_url and cfg.gcs_bucket_name and cfg.vertex_sa_json:
            try:
                from providers.gcs_storage import upload_media_urls
                gcs_urls = upload_media_urls(
                    cfg.vertex_sa_json, cfg.gcs_bucket_name, [audio_url], prefix="elevenlabs",
                )
                if gcs_urls:
                    audio_url = gcs_urls[0]
            except Exception:
                pass

        # 로딩 아이템 업데이트
        for item in st.session_state.get("elevenlabs_history", []):
            if item.get("item_id") == pending["item_id"] and item.get("loading"):
                item["audio_url"] = audio_url
                item["loading"] = False
                if _is_authenticated() and audio_url:
                    try:
                        update_elevenlabs_audio_url(cfg, pending["item_id"], audio_url)
                    except Exception:
                        pass
                break
        st.rerun()

    # ── 에러 메시지 표시 (이전 rerun에서 저장된 것) ──
    _err = st.session_state.pop("_el_error_msg", None)
    if _err:
        st.toast(_err, icon="⚠️")
        from core.db import insert_error_log
        insert_error_log(cfg, st.session_state.get("user_id", ""), st.session_state.get("school_id", "default"), "elevenlabs", _err)

    _cred = st.session_state.pop("_el_credit_toast", None)
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
    if st.session_state.get("_el_gallery_open"):
        school_id = st.session_state.get("school_id", "default")
        school_gallery = load_school_elevenlabs_gallery(cfg, school_id)

    history = st.session_state.get("elevenlabs_history", [])
    clone_result_arg = st.session_state.pop("_el_clone_result", None)

    # 클론 보이스 목록 (캐시 — 변경 시 갱신)
    cloned_voices = st.session_state.get("_el_cloned_voices")
    if cloned_voices is None and not sidebar.test_mode:
        try:
            all_voices = call_with_lease(
                cfg, test_mode=False, provider="elevenlabs",
                mock_fn=lambda: [],
                real_fn=lambda kp: elevenlabs.list_voices(api_key=kp["api_key"]),
            )
            cloned_voices = [v for v in all_voices if v.get("category") == "cloned"]
            st.session_state["_el_cloned_voices"] = cloned_voices
        except Exception:
            cloned_voices = []
    elif cloned_voices is None:
        cloned_voices = []

    result = _elevenlabs_component(frame_height=900, history=history,
                                   enabled_features=_get_tab_features(cfg, "elevenlabs."),
                                   school_gallery=school_gallery,
                                   default_model=cfg.elevenlabs_model,
                                   clone_result=clone_result_arg,
                                   cloned_voices=cloned_voices)

    if not result or not isinstance(result, dict):
        return

    action = result.get("action")
    ts = result.get("ts", 0)

    # 중복 실행 방지: 처리 완료된 action key set으로 체크
    _item_id = result.get("item_id", "")
    dedup_key = f"{action}_{ts}_{_item_id}"
    _processed = st.session_state.setdefault("_el_processed_actions", set())
    if dedup_key in _processed:
        return
    _processed.add(dedup_key)
    if len(_processed) > 500:
        st.session_state["_el_processed_actions"] = {dedup_key}

    if action == "open_gallery":
        st.session_state["_el_gallery_open"] = True
        st.rerun()
    elif action == "close_gallery":
        st.session_state["_el_gallery_open"] = False
        st.rerun()
    elif action == "generate":
        if not _is_authenticated():
            return
        # 이미 대기 중인 요청이 있으면 무시 (중복 방지)
        if st.session_state.get("_el_pending_generate"):
            return

        # ── 크레딧 확인 (Phase 1) ──
        from core.credits import check_credits, get_feature_cost
        _cost = get_feature_cost(cfg, "el_tts")
        ok, msg = check_credits(cfg, _cost)
        if not ok:
            st.session_state["_el_error_msg"] = msg
            st.rerun()
            return

        text = result.get("text", "")
        if len(text) > 10000:
            text = text[:10000]
        voice_id = result.get("voice_id", "")
        model_id = result.get("model_id", "eleven_multilingual_v2")
        settings = result.get("settings", {})
        speaker_boost = result.get("speaker_boost", False)
        item_id = result.get("item_id")

        if not sidebar.test_mode:
            # Real API → 로딩 아이템 먼저 표시, 다음 rerun에서 API 호출
            new_item = {
                "item_id": item_id,
                "text": text,
                "voice_id": voice_id,
                "voice_name": result.get("voice_name"),
                "model_id": model_id,
                "model_label": result.get("model_label"),
                "settings": settings,
                "language_override": result.get("language_override", False),
                "speaker_boost": speaker_boost,
                "audio_url": None,
                "loading": True,
                "loading_ts": ts,
            }

            if _is_authenticated():
                try:
                    insert_elevenlabs_item(
                        cfg, st.session_state["user_id"], new_item,
                    )
                except Exception:
                    pass

            hist = st.session_state.setdefault("elevenlabs_history", [])
            hist.insert(0, new_item)
            if len(hist) > 50:
                del hist[50:]

            # 다음 rerun에서 처리할 대기 요청 저장
            st.session_state["_el_pending_generate"] = {
                "pending_action": "tts",
                "item_id": item_id,
                "text": text,
                "voice_id": voice_id,
                "model_id": model_id,
                "settings": settings,
                "speaker_boost": speaker_boost,
            }
        else:
            # Mock ON → 기존 동작 유지 (JS가 mock 완료 이벤트 전달)
            new_item = {
                "item_id": item_id,
                "text": text,
                "voice_id": voice_id,
                "voice_name": result.get("voice_name"),
                "model_id": model_id,
                "model_label": result.get("model_label"),
                "settings": settings,
                "language_override": result.get("language_override", False),
                "speaker_boost": speaker_boost,
                "audio_url": None,
                "loading": True,
                "loading_ts": ts,
            }

            if _is_authenticated():
                try:
                    insert_elevenlabs_item(
                        cfg, st.session_state["user_id"], new_item,
                    )
                except Exception:
                    pass

            hist = st.session_state.setdefault("elevenlabs_history", [])
            hist.insert(0, new_item)
            if len(hist) > 50:
                del hist[50:]

        st.rerun()

    elif action == "voice_to_voice":
        if st.session_state.get("_el_pending_generate"):
            return
        from core.credits import check_credits, get_feature_cost
        _cost = get_feature_cost(cfg, "el_vtv")
        ok, msg = check_credits(cfg, _cost)
        if not ok:
            st.session_state["_el_error_msg"] = msg
            st.rerun()
            return

        item_id = result.get("item_id")
        audio_data = result.get("audio_data", "")  # data:audio/...;base64,...
        # base64 부분만 추출
        audio_b64 = audio_data.split(",", 1)[1] if "," in audio_data else audio_data

        new_item = {
            "item_id": item_id,
            "text": f"[Voice Changer] {result.get('audio_name', 'audio')}",
            "voice_id": result.get("voice_id", ""),
            "voice_name": result.get("voice_name", ""),
            "model_id": cfg.elevenlabs_vtv_model,
            "model_label": "Voice-to-Voice",
            "settings": result.get("settings", {}),
            "speaker_boost": result.get("speaker_boost", True),
            "audio_url": None,
            "loading": True,
            "loading_ts": ts,
        }
        if _is_authenticated():
            try:
                insert_elevenlabs_item(cfg, st.session_state["user_id"], new_item)
            except Exception:
                pass
        hist = st.session_state.setdefault("elevenlabs_history", [])
        hist.insert(0, new_item)
        if len(hist) > 50:
            del hist[50:]
        if not sidebar.test_mode:
            st.session_state["_el_pending_generate"] = {
                "pending_action": "vtv",
                "item_id": item_id,
                "voice_id": result.get("voice_id", ""),
                "audio_b64": audio_b64,
                "model_id": cfg.elevenlabs_vtv_model,
                "settings": result.get("settings", {}),
                "speaker_boost": result.get("speaker_boost", True),
            }
        st.rerun()

    elif action == "sound_generation":
        if st.session_state.get("_el_pending_generate"):
            return
        from core.credits import check_credits, get_feature_cost
        _cost = get_feature_cost(cfg, "el_sfx")
        ok, msg = check_credits(cfg, _cost)
        if not ok:
            st.session_state["_el_error_msg"] = msg
            st.rerun()
            return

        item_id = result.get("item_id")
        text = result.get("text", "")
        sfx_duration = result.get("duration_seconds")
        sfx_influence = result.get("prompt_influence", 0.3)

        new_item = {
            "item_id": item_id,
            "text": text,
            "voice_id": "",
            "voice_name": "",
            "model_id": "sound_generation",
            "model_label": "Sound Effects",
            "settings": {},
            "speaker_boost": False,
            "duration_seconds": sfx_duration,
            "prompt_influence": sfx_influence,
            "audio_url": None,
            "loading": True,
            "loading_ts": ts,
        }
        if _is_authenticated():
            try:
                insert_elevenlabs_item(cfg, st.session_state["user_id"], new_item)
            except Exception:
                pass
        hist = st.session_state.setdefault("elevenlabs_history", [])
        hist.insert(0, new_item)
        if len(hist) > 50:
            del hist[50:]
        if not sidebar.test_mode:
            st.session_state["_el_pending_generate"] = {
                "pending_action": "sfx",
                "item_id": item_id,
                "text": text,
                "duration_seconds": sfx_duration,
                "prompt_influence": sfx_influence,
            }
        st.rerun()

    elif action == "voice_clone":
        if st.session_state.get("_el_pending_generate"):
            return
        from core.credits import check_credits, get_feature_cost
        _cost = get_feature_cost(cfg, "el_clone")
        ok, msg = check_credits(cfg, _cost)
        if not ok:
            st.session_state["_el_error_msg"] = msg
            st.rerun()
            return

        item_id = result.get("item_id")
        audio_data = result.get("audio_data", "")
        audio_b64 = audio_data.split(",", 1)[1] if "," in audio_data else audio_data
        audio_name = result.get("audio_name", "My Voice")

        if sidebar.test_mode:
            # Mock 모드: 가짜 clone 결과를 세션에 저장 (history에 넣지 않음)
            import uuid as _uuid
            mock_voice_id = f"mock_clone_{_uuid.uuid4().hex[:8]}"
            mock_name = audio_name.rsplit(".", 1)[0] if "." in audio_name else audio_name
            st.session_state["_el_clone_result"] = {
                "voice_id": mock_voice_id, "name": mock_name,
            }
        else:
            st.session_state["_el_pending_generate"] = {
                "pending_action": "clone",
                "item_id": item_id,
                "audio_b64": audio_b64,
                "clone_name": audio_name.rsplit(".", 1)[0] if "." in audio_name else audio_name,
            }
        st.rerun()

    # ── 로딩 완료 이벤트 ──
    elif action == "loading_complete":
        item_id = result.get("item_id")
        audio_url = result.get("audio_url", "")
        updated = False
        for item in st.session_state.get("elevenlabs_history", []):
            if item.get("item_id") == item_id and item.get("loading"):
                item["loading"] = False
                item["audio_url"] = audio_url
                updated = True
                break
        if updated:
            if _is_authenticated() and audio_url:
                try:
                    update_elevenlabs_audio_url(cfg, item_id, audio_url)
                except Exception:
                    pass
            st.rerun()

    # ── 클론 보이스 삭제 ──
    elif action == "delete_voice":
        voice_id = result.get("voice_id", "")
        if voice_id and not sidebar.test_mode:
            try:
                ok = call_with_lease(
                    cfg, test_mode=False, provider="elevenlabs",
                    mock_fn=lambda: True,
                    real_fn=lambda kp: elevenlabs.delete_voice(
                        api_key=kp["api_key"], voice_id=voice_id,
                    ),
                )
                if ok:
                    st.session_state["_el_cloned_voices"] = None  # 캐시 갱신
                    st.toast("보이스가 삭제되었습니다.", icon="🗑️")
                else:
                    st.session_state["_el_error_msg"] = "보이스 삭제에 실패했습니다."
            except Exception as e:
                st.session_state["_el_error_msg"] = f"삭제 오류: {e}"
        elif voice_id and sidebar.test_mode:
            # Mock: 캐시에서 제거
            cached = st.session_state.get("_el_cloned_voices") or []
            st.session_state["_el_cloned_voices"] = [
                v for v in cached if v.get("voice_id") != voice_id
            ]
            st.toast("(Mock) 보이스가 삭제되었습니다.", icon="🗑️")
        st.rerun()


TAB = {
    "tab_id": "elevenlabs",
    "title": "ElevenLabs",
    "required_features": {"tab.elevenlabs"},
    "render": render_elevenlabs_tab,
}
