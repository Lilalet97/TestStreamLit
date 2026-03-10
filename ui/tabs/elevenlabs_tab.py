# ui/tabs/elevenlabs_tab.py
"""ElevenLabs Text-to-Speech 페이지 — declare_component 양방향 통신."""
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
):
    """ElevenLabs 커스텀 컴포넌트 래퍼."""
    return _elevenlabs_component_func(
        frame_height=frame_height,
        history=history or [],
        enabled_features=enabled_features or [],
        school_gallery=school_gallery,
        default_model=default_model,
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
        try:
            audio_url = call_with_lease(
                cfg,
                test_mode=False,
                provider="elevenlabs",
                mock_fn=lambda: None,
                real_fn=lambda kp: elevenlabs.text_to_speech(
                    api_key=kp["api_key"],
                    voice_id=pending["voice_id"],
                    text=pending["text"],
                    model_id=cfg.elevenlabs_model,
                    stability=float(pending["settings"].get("stability", 0.5)),
                    similarity_boost=float(pending["settings"].get("similarity_boost", 0.75)),
                    style=float(pending["settings"].get("style", 0.0)),
                    use_speaker_boost=pending["speaker_boost"],
                ),
            )
            # GCS 업로드 (설정 시)
            if audio_url and cfg.gcs_bucket_name and cfg.vertex_sa_json:
                from providers.gcs_storage import upload_single_media_url
                audio_url = upload_single_media_url(
                    cfg.vertex_sa_json, cfg.gcs_bucket_name, audio_url, prefix="elevenlabs",
                )
        except Exception as e:
            audio_url = None
            st.session_state["_el_error_msg"] = f"ElevenLabs API 오류: {e}"

        # ── 크레딧 차감 (Phase 2) ──
        if audio_url:
            from core.credits import deduct_after_success, get_feature_cost
            try:
                _cost = get_feature_cost(cfg, "elevenlabs")
                new_bal = deduct_after_success(cfg, _cost, tab_id="elevenlabs")
                if new_bal >= 0:
                    st.session_state["_el_credit_toast"] = new_bal
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
    result = _elevenlabs_component(frame_height=900, history=history,
                                   enabled_features=_get_tab_features(cfg, "elevenlabs."),
                                   school_gallery=school_gallery,
                                   default_model=cfg.elevenlabs_model)

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
    if len(_processed) > 100:
        st.session_state["_el_processed_actions"] = {dedup_key}

    if action == "open_gallery":
        st.session_state["_el_gallery_open"] = True
        st.rerun()
    elif action == "close_gallery":
        st.session_state["_el_gallery_open"] = False
        st.rerun()
    elif action == "generate":
        # 이미 대기 중인 요청이 있으면 무시 (중복 방지)
        if st.session_state.get("_el_pending_generate"):
            return

        # ── 크레딧 확인 (Phase 1) ──
        from core.credits import check_credits, get_feature_cost
        _cost = get_feature_cost(cfg, "elevenlabs")
        ok, msg = check_credits(cfg, _cost)
        if not ok:
            st.session_state["_el_error_msg"] = msg
            st.rerun()
            return

        text = result.get("text", "")
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

            st.session_state.setdefault("elevenlabs_history", []).insert(0, new_item)

            # 다음 rerun에서 처리할 대기 요청 저장
            st.session_state["_el_pending_generate"] = {
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

            st.session_state.setdefault("elevenlabs_history", []).insert(0, new_item)

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


TAB = {
    "tab_id": "elevenlabs",
    "title": "🔊 ElevenLabs",
    "required_features": {"tab.elevenlabs"},
    "render": render_elevenlabs_tab,
}
