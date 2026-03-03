# ui/tabs/nanobanana_tab.py
"""NanoBanana 이미지 생성 페이지 — 멀티턴 편집 세션 (GPT 탭 패턴)."""
import uuid
import random
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from core.config import AppConfig
from core.api_bridge import call_with_lease
from core.db import (
    upsert_nanobanana_session,
    load_nanobanana_sessions,
    delete_nanobanana_session,
    load_school_nanobanana_gallery,
)
from providers import google_imagen
from ui.sidebar import SidebarState

_COMPONENT_DIR = Path(__file__).resolve().parent / "templates" / "nanobanana"
_nanobanana_component_func = components.declare_component("nanobanana_component", path=str(_COMPONENT_DIR))

_ASPECT_SIZES = {
    "1:1": (1024, 1024),
    "16:9": (1024, 576),
    "9:16": (576, 1024),
    "4:3": (1024, 768),
    "3:4": (768, 1024),
}


def _mock_image_urls(aspect_ratio: str, num_images: int) -> list[str]:
    """picsum.photos 기반 mock 이미지 URL 생성."""
    w, h = _ASPECT_SIZES.get(aspect_ratio, (1024, 1024))
    return [
        f"https://picsum.photos/seed/nb{random.randint(1, 99999)}/{w}/{h}"
        for _ in range(num_images)
    ]


def _edit_each_image(
    api_key: str,
    prompt: str, source_images: list[str], aspect_ratio: str,
    # [VERTEX AI] sa_json: str = "", project_id: str = "", location: str = "",
) -> list[str]:
    """각 소스 이미지를 개별적으로 Gemini 호출하여 1:1 편집."""
    from providers.gcs_storage import resolve_to_data_url

    results: list[str] = []
    for img_url in source_images:
        resolved = resolve_to_data_url(img_url)  # GCS URL → data URL 변환
        edited = google_imagen.gemini_generate(
            api_key=api_key,
            parts=[{"text": prompt}, resolved],
            aspect_ratio=aspect_ratio,
            num_images=1,
            # [VERTEX AI] sa_json=sa_json, project_id=project_id, location=location,
        )
        results.extend(edited)
    return results


def _is_authenticated() -> bool:
    return (
        st.session_state.get("auth_logged_in", False)
        and st.session_state.get("user_id", "guest") != "guest"
    )


def _init_state(cfg: AppConfig):
    """세션 상태 초기화: 로그인 사용자는 DB에서 로드."""
    if "nb_sessions" in st.session_state and st.session_state.get("_nb_db_loaded"):
        return

    if _is_authenticated():
        sessions = load_nanobanana_sessions(cfg, st.session_state["user_id"])
        if sessions:
            st.session_state.nb_sessions = sessions
            st.session_state.nb_active_id = sessions[0]["id"]
            st.session_state["_nb_db_loaded"] = True
            return

    if "nb_sessions" not in st.session_state:
        st.session_state.nb_sessions = []
        st.session_state.nb_active_id = ""
    st.session_state["_nb_db_loaded"] = True


def _nanobanana_component(
    sessions: list,
    active_id: str,
    frame_height: int = 900,
    key: str = "nanobanana_main",
    enabled_features: list | None = None,
    school_gallery: list | None = None,
):
    """NanoBanana 커스텀 컴포넌트 래퍼."""
    return _nanobanana_component_func(
        sessions=sessions,
        active_id=active_id,
        frame_height=frame_height,
        enabled_features=enabled_features or [],
        school_gallery=school_gallery,
        key=key,
        default=None,
    )


def _auto_title(turns: list) -> str:
    """첫 턴의 프롬프트 앞 30자를 제목으로."""
    for t in turns:
        if t.get("prompt", "").strip():
            text = t["prompt"].strip()
            return text[:30] + ("..." if len(text) > 30 else "")
    return "New Image"


def _find_session_or_create(model_id: str) -> dict:
    """활성 세션 찾기, 없으면 새로 생성."""
    for s in st.session_state.nb_sessions:
        if s["id"] == st.session_state.nb_active_id:
            return s
    new_id = str(uuid.uuid4())
    session = {
        "id": new_id,
        "title": "New Image",
        "model": model_id,
        "turns": [],
    }
    st.session_state.nb_sessions.insert(0, session)
    st.session_state.nb_active_id = new_id
    return session


def _add_turn_to_session(session: dict, new_turn: dict):
    """턴 추가 + 세션 최상단 이동 + 제목 자동 설정."""
    new_turn["is_edit"] = len(session["turns"]) > 0
    session["turns"].append(new_turn)
    session["model"] = new_turn.get("model_id", session["model"])

    if session["title"] == "New Image" and session["turns"]:
        session["title"] = _auto_title(session["turns"])

    st.session_state.nb_sessions = [session] + [
        s for s in st.session_state.nb_sessions if s["id"] != session["id"]
    ]


def _get_tab_features(cfg: AppConfig, prefix: str) -> list:
    school_id = st.session_state.get("school_id", "default")
    return [f for f in cfg.get_enabled_features(school_id) if f.startswith(prefix)]


def render_nanobanana_tab(cfg: AppConfig, sidebar: SidebarState):
    """NanoBanana 이미지 생성 탭 (멀티턴 세션)."""
    _init_state(cfg)

    # ── 대기 중인 생성 요청 처리 (2단계: 실제 API 호출) ──
    pending = st.session_state.get("_nb_pending_generate")
    if pending:
        del st.session_state["_nb_pending_generate"]
        source_images = pending.get("source_images", [])
        try:
            if source_images:
                # 편집 모드: 각 이미지별 개별 Gemini 호출 → 1:1 편집
                image_urls = call_with_lease(
                    cfg,
                    test_mode=False,
                    provider="google_imagen",
                    mock_fn=lambda: _mock_image_urls(pending["ar"], len(source_images)),
                    real_fn=lambda kp: _edit_each_image(
                        kp["api_key"],
                        pending["prompt"], source_images, pending["ar"],
                        # [VERTEX AI] kp["sa_json"], kp["project_id"], kp["location"],
                    ),
                )
            else:
                # 생성 모드: Gemini로 텍스트만 생성
                image_urls = call_with_lease(
                    cfg,
                    test_mode=False,
                    provider="google_imagen",
                    mock_fn=lambda: _mock_image_urls(pending["ar"], pending["num"]),
                    real_fn=lambda kp: google_imagen.gemini_generate(
                        api_key=kp["api_key"],
                        parts=[{"text": pending["prompt"]}],
                        aspect_ratio=pending["ar"],
                        num_images=pending["num"],
                        # [VERTEX AI] sa_json=kp["sa_json"], project_id=kp["project_id"], location=kp["location"],
                    ),
                )
            # GCS 업로드 (설정 시)
            if image_urls and cfg.gcs_bucket_name and cfg.vertex_sa_json:
                from providers.gcs_storage import upload_media_urls
                image_urls = upload_media_urls(
                    cfg.vertex_sa_json, cfg.gcs_bucket_name, image_urls, prefix="nanobanana",
                )
        except Exception as e:
            image_urls = []
            st.session_state["_nb_error_msg"] = f"이미지 API 오류: {e}"

        # 로딩 턴 업데이트
        for s in st.session_state.nb_sessions:
            if s["id"] == pending["session_id"]:
                for t in s["turns"]:
                    if t["turn_id"] == pending["turn_id"]:
                        t["image_urls"] = image_urls
                        t["loading"] = False
                        break
                if _is_authenticated():
                    try:
                        upsert_nanobanana_session(
                            cfg, st.session_state["user_id"], s,
                        )
                    except Exception:
                        pass
                break
        st.rerun()

    # ── 에러 메시지 표시 (이전 rerun에서 저장된 것) ──
    _err = st.session_state.pop("_nb_error_msg", None)
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

    # 학교 공유 갤러리 데이터 로드
    school_gallery = None
    if st.session_state.get("_nb_gallery_open"):
        school_id = st.session_state.get("school_id", "default")
        school_gallery = load_school_nanobanana_gallery(cfg, school_id)

    sessions = st.session_state.get("nb_sessions", [])
    active_id = st.session_state.get("nb_active_id", "")
    result = _nanobanana_component(sessions=sessions, active_id=active_id,
                                   enabled_features=_get_tab_features(cfg, "nanobanana."),
                                   school_gallery=school_gallery)

    if not result or not isinstance(result, dict):
        return

    action = result.get("action")
    ts = result.get("ts", 0)

    # 중복 실행 방지: 처리 완료된 action key set으로 체크
    item_id = result.get("item_id", "")
    dedup_key = f"{action}_{ts}_{item_id}"
    _processed = st.session_state.setdefault("_nb_processed_actions", set())
    if dedup_key in _processed:
        return
    _processed.add(dedup_key)
    if len(_processed) > 100:
        st.session_state["_nb_processed_actions"] = {dedup_key}

    if action == "open_gallery":
        st.session_state["_nb_gallery_open"] = True
        st.rerun()
    elif action == "close_gallery":
        st.session_state["_nb_gallery_open"] = False
        st.rerun()

    # ── generate: 활성 세션에 턴 추가 (세션 없으면 자동 생성) ──
    elif action == "generate":
        # 이미 대기 중인 요청이 있으면 무시 (중복 방지)
        if st.session_state.get("_nb_pending_generate"):
            return

        ar = result.get("aspect_ratio", "1:1")
        num = result.get("num_images", 1)
        prompt_text = result.get("prompt", "")
        negative_prompt = result.get("negative_prompt", "")
        model_id = result.get("model_id", "imagen-4.0-generate-001")

        session = _find_session_or_create(model_id)

        # 직전 턴의 모든 이미지 가져오기 (편집 모드용)
        source_images = []
        if session["turns"]:
            last_turn = session["turns"][-1]
            if last_turn.get("image_urls"):
                source_images = list(last_turn["image_urls"])

        if not sidebar.test_mode:
            # Real API → 로딩 턴 먼저 표시, 다음 rerun에서 API 호출
            new_turn = {
                "turn_id": result.get("item_id", f"nb_{ts}"),
                "prompt": prompt_text,
                "model_id": model_id,
                "model_label": result.get("model_label"),
                "aspect_ratio": ar,
                "num_images": num,
                "style_preset": result.get("style_preset"),
                "negative_prompt": negative_prompt,
                "settings": result.get("settings", {}),
                "image_urls": [],
                "is_edit": False,
                "loading": True,
            }
            _add_turn_to_session(session, new_turn)

            # 다음 rerun에서 처리할 대기 요청 저장
            pending_data = {
                "session_id": session["id"],
                "turn_id": new_turn["turn_id"],
                "ar": ar,
                "num": num,
                "prompt": prompt_text,
            }
            if source_images:
                pending_data["source_images"] = source_images
            st.session_state["_nb_pending_generate"] = pending_data
        else:
            # Mock → 즉시 결과
            new_turn = {
                "turn_id": result.get("item_id", f"nb_{ts}"),
                "prompt": prompt_text,
                "model_id": model_id,
                "model_label": result.get("model_label"),
                "aspect_ratio": ar,
                "num_images": num,
                "style_preset": result.get("style_preset"),
                "negative_prompt": negative_prompt,
                "settings": result.get("settings", {}),
                "image_urls": _mock_image_urls(ar, num),
                "is_edit": False,
                "loading": False,
            }
            _add_turn_to_session(session, new_turn)

        if _is_authenticated():
            try:
                upsert_nanobanana_session(cfg, st.session_state["user_id"], session)
            except Exception:
                pass

        st.rerun()

    # ── new_session ──
    elif action == "new_session":
        new_id = str(uuid.uuid4())
        new_session = {
            "id": new_id,
            "title": "New Image",
            "model": "imagen-4.0-generate-001",
            "turns": [],
        }
        st.session_state.nb_sessions.insert(0, new_session)
        st.session_state.nb_active_id = new_id
        st.rerun()

    # ── switch_session ──
    elif action == "switch_session":
        st.session_state.nb_active_id = result.get("session_id")
        st.rerun()

    # ── delete_session ──
    elif action == "delete_session":
        session_id = result.get("session_id")
        st.session_state.nb_sessions = [
            s for s in st.session_state.nb_sessions if s["id"] != session_id
        ]
        if _is_authenticated():
            try:
                delete_nanobanana_session(cfg, st.session_state["user_id"], session_id)
            except Exception:
                pass

        if st.session_state.get("nb_active_id") == session_id:
            if st.session_state.nb_sessions:
                st.session_state.nb_active_id = st.session_state.nb_sessions[0]["id"]
            else:
                st.session_state.nb_active_id = ""

        st.rerun()


TAB = {
    "tab_id": "nanobanana",
    "title": "\U0001f34c NanoBanana",
    "required_features": {"tab.nanobanana"},
    "render": render_nanobanana_tab,
}
