# ui/tabs/_nanobanana_factory.py
"""NanoBanana 탭 변형 팩토리 — 모델만 다른 여러 탭을 생성."""
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
    load_mj_gallery,
)
from providers import google_imagen
from ui.sidebar import SidebarState

_ASPECT_SIZES = {
    "1:1": (1024, 1024),
    "16:9": (1024, 576),
    "9:16": (576, 1024),
    "4:3": (1024, 768),
    "3:4": (768, 1024),
}


def _mock_image_urls(aspect_ratio: str, num_images: int) -> list[str]:
    w, h = _ASPECT_SIZES.get(aspect_ratio, (1024, 1024))
    return [
        f"https://picsum.photos/seed/nb{random.randint(1, 99999)}/{w}/{h}"
        for _ in range(num_images)
    ]


def _edit_each_image(
    api_key: str,
    prompt: str, source_images: list[str], aspect_ratio: str,
    model: str = "",
) -> list[str]:
    from providers.gcs_storage import resolve_to_data_url

    _model = model or google_imagen.EDIT_MODEL
    results: list[str] = []
    for img_url in source_images:
        resolved = resolve_to_data_url(img_url)
        edited = google_imagen.gemini_generate(
            api_key=api_key,
            parts=[{"text": prompt}, resolved],
            aspect_ratio=aspect_ratio,
            num_images=1,
            model=_model,
        )
        results.extend(edited)
    return results


def _is_authenticated() -> bool:
    return (
        st.session_state.get("auth_logged_in", False)
        and st.session_state.get("user_id", "guest") != "guest"
    )


def _auto_title(turns: list) -> str:
    for t in turns:
        if t.get("prompt", "").strip():
            text = t["prompt"].strip()
            return text[:30] + ("..." if len(text) > 30 else "")
    return "New Image"


def make_nanobanana_variant(
    *,
    tab_id: str,
    title: str,
    feature_key: str,
    get_model: callable,       # cfg -> str  (모델 ID 반환)
    state_prefix: str,         # e.g. "nb", "nbp", "nb2"
    template_subdir: str,      # e.g. "nanobanana", "nanobanana_pro"
    component_name: str,       # e.g. "nanobanana_component"
    credit_feature: str,       # e.g. "nanobanana", "nanobanana_pro"
) -> dict:
    """NanoBanana 변형 탭 TAB dict를 생성하는 팩토리."""

    # 템플릿 디렉토리 & 컴포넌트 선언
    _comp_dir = Path(__file__).resolve().parent / "templates" / template_subdir
    _comp_func = components.declare_component(component_name, path=str(_comp_dir))

    # ── 세션 상태 키 ──
    K_SESSIONS = f"{state_prefix}_sessions"
    K_ACTIVE = f"{state_prefix}_active_id"
    K_DB_LOADED = f"_{state_prefix}_db_loaded"
    K_PENDING = f"_{state_prefix}_pending_generate"
    K_ERROR = f"_{state_prefix}_error_msg"
    K_CREDIT = f"_{state_prefix}_credit_toast"
    K_GALLERY = f"_{state_prefix}_gallery_open"
    K_PROCESSED = f"_{state_prefix}_processed_actions"
    COMP_KEY = f"{state_prefix}_main"

    def _init_state(cfg: AppConfig):
        if K_SESSIONS in st.session_state and st.session_state.get(K_DB_LOADED):
            return
        if _is_authenticated():
            sessions = load_nanobanana_sessions(cfg, st.session_state["user_id"], tab_id=tab_id)
            if sessions:
                st.session_state[K_SESSIONS] = sessions
                st.session_state[K_ACTIVE] = sessions[0]["id"]
                st.session_state[K_DB_LOADED] = True
                return
        if K_SESSIONS not in st.session_state:
            st.session_state[K_SESSIONS] = []
            st.session_state[K_ACTIVE] = ""
        st.session_state[K_DB_LOADED] = True

    def _component_wrapper(sessions, active_id, frame_height=900, enabled_features=None, school_gallery=None, source_gallery=None, default_model=""):
        return _comp_func(
            sessions=sessions,
            active_id=active_id,
            frame_height=frame_height,
            enabled_features=enabled_features or [],
            school_gallery=school_gallery,
            source_gallery=source_gallery or [],
            default_model=default_model,
            key=COMP_KEY,
            default=None,
        )

    def _find_session_or_create(model_id: str) -> dict:
        for s in st.session_state[K_SESSIONS]:
            if s["id"] == st.session_state[K_ACTIVE]:
                return s
        new_id = str(uuid.uuid4())
        session = {"id": new_id, "title": "New Image", "model": model_id, "turns": []}
        st.session_state[K_SESSIONS].insert(0, session)
        st.session_state[K_ACTIVE] = new_id
        return session

    def _add_turn_to_session(session: dict, new_turn: dict):
        new_turn["is_edit"] = len(session["turns"]) > 0
        session["turns"].append(new_turn)
        session["model"] = new_turn.get("model_id", session["model"])
        if session["title"] == "New Image" and session["turns"]:
            session["title"] = _auto_title(session["turns"])
        st.session_state[K_SESSIONS] = [session] + [
            s for s in st.session_state[K_SESSIONS] if s["id"] != session["id"]
        ]

    def _get_tab_features(cfg: AppConfig, prefix: str) -> list:
        school_id = st.session_state.get("school_id", "default")
        features = [f for f in cfg.get_enabled_features(school_id) if f.startswith(prefix)]
        # nanobanana_2, nanobanana_pro도 공통 nanobanana.* 피처를 상속
        if prefix != "nanobanana." and prefix.startswith("nanobanana"):
            features += [f for f in cfg.get_enabled_features(school_id) if f.startswith("nanobanana.")]
        return list(set(features))

    def render(cfg: AppConfig, sidebar: SidebarState):
        _init_state(cfg)
        model_id = get_model(cfg)

        # ── 대기 중인 생성 요청 처리 ──
        pending = st.session_state.get(K_PENDING)
        if pending:
            del st.session_state[K_PENDING]
            source_images = pending.get("source_images", [])
            try:
                if source_images:
                    image_urls = call_with_lease(
                        cfg, test_mode=False, provider="google_imagen",
                        mock_fn=lambda: _mock_image_urls(pending["ar"], len(source_images)),
                        real_fn=lambda kp: _edit_each_image(
                            kp["api_key"], pending["prompt"], source_images, pending["ar"],
                            model=model_id,
                        ),
                        model=model_id,
                    )
                else:
                    gen_parts = []
                    ref_img = pending.get("reference_image", "")
                    neg = pending.get("negative_prompt", "")
                    style = pending.get("style_preset", "")
                    prompt_text = pending["prompt"]
                    if style:
                        prompt_text = f"[Style: {style}] {prompt_text}"
                    if neg:
                        prompt_text = f"{prompt_text}. Avoid: {neg}"
                    if ref_img:
                        gen_parts.append({"text": "Edit the first image. " + prompt_text})
                        gen_parts.append(ref_img)
                    else:
                        gen_parts.append({"text": prompt_text})
                    image_urls = call_with_lease(
                        cfg, test_mode=False, provider="google_imagen",
                        mock_fn=lambda: _mock_image_urls(pending["ar"], pending["num"]),
                        real_fn=lambda kp: google_imagen.gemini_generate(
                            api_key=kp["api_key"],
                            parts=gen_parts,
                            aspect_ratio=pending["ar"],
                            num_images=pending["num"],
                            model=model_id,
                        ),
                        model=model_id,
                    )
                if image_urls and cfg.gcs_bucket_name and cfg.vertex_sa_json:
                    from providers.gcs_storage import upload_media_urls
                    image_urls = upload_media_urls(
                        cfg.vertex_sa_json, cfg.gcs_bucket_name, image_urls, prefix="nanobanana",
                    )
            except Exception as e:
                image_urls = []
                st.session_state[K_ERROR] = f"이미지 API 오류: {e}"

            if image_urls:
                from core.credits import deduct_after_success, get_feature_cost
                try:
                    _cost = get_feature_cost(cfg, credit_feature)
                    new_bal = deduct_after_success(cfg, _cost, tab_id=tab_id)
                    if new_bal >= 0:
                        st.session_state[K_CREDIT] = new_bal
                except Exception:
                    pass

            for s in st.session_state[K_SESSIONS]:
                if s["id"] == pending["session_id"]:
                    for t in s["turns"]:
                        if t["turn_id"] == pending["turn_id"]:
                            t["image_urls"] = image_urls
                            t["loading"] = False
                            break
                    if _is_authenticated():
                        try:
                            upsert_nanobanana_session(cfg, st.session_state["user_id"], s, tab_id=tab_id)
                        except Exception:
                            pass
                    break
            st.rerun()

        _err = st.session_state.pop(K_ERROR, None)
        if _err:
            st.toast(_err, icon="⚠️")
            from core.db import insert_error_log
            insert_error_log(cfg, st.session_state.get("user_id", ""), st.session_state.get("school_id", "default"), tab_id, _err)
        _cred = st.session_state.pop(K_CREDIT, None)
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

        school_gallery = None
        if st.session_state.get(K_GALLERY):
            school_id = st.session_state.get("school_id", "default")
            school_gallery = load_school_nanobanana_gallery(cfg, school_id)

        # source_gallery: MJ + NB 이미지 로드 (갤러리 피커용)
        source_gallery = []
        if st.session_state.get("user_id"):
            try:
                # MJ 이미지
                mj_items = load_mj_gallery(cfg, st.session_state["user_id"], limit=50)
                for item in mj_items:
                    for url in (item.get("images") or []):
                        if url:
                            source_gallery.append({
                                "source": "mj",
                                "prompt": (item.get("prompt") or "")[:60],
                                "url": url,
                            })
                # NB 이미지 (자기 자신 포함)
                nb_sessions = load_nanobanana_sessions(cfg, st.session_state["user_id"], limit=20, tab_id=None)
                for sess in nb_sessions:
                    for turn in (sess.get("turns") or []):
                        for url in (turn.get("image_urls") or []):
                            if url:
                                source_gallery.append({
                                    "source": "nanobanana",
                                    "prompt": (turn.get("prompt") or "")[:60],
                                    "url": url,
                                })
            except Exception:
                pass

        sessions = st.session_state.get(K_SESSIONS, [])
        active_id = st.session_state.get(K_ACTIVE, "")
        result = _component_wrapper(
            sessions=sessions, active_id=active_id,
            enabled_features=_get_tab_features(cfg, f"{credit_feature}."),
            school_gallery=school_gallery,
            source_gallery=source_gallery,
            default_model=model_id,
        )

        if not result or not isinstance(result, dict):
            return

        action = result.get("action")
        ts = result.get("ts", 0)
        item_id = result.get("item_id", "")
        dedup_key = f"{action}_{ts}_{item_id}"
        _processed = st.session_state.setdefault(K_PROCESSED, set())
        if dedup_key in _processed:
            return
        _processed.add(dedup_key)
        if len(_processed) > 500:
            st.session_state[K_PROCESSED] = {dedup_key}

        if action == "open_gallery":
            st.session_state[K_GALLERY] = True
            st.rerun()
        elif action == "close_gallery":
            st.session_state[K_GALLERY] = False
            st.rerun()

        elif action == "generate":
            if not _is_authenticated():
                return
            if st.session_state.get(K_PENDING):
                return

            from core.credits import check_credits, get_feature_cost
            _cost = get_feature_cost(cfg, credit_feature)
            ok, msg = check_credits(cfg, _cost)
            if not ok:
                st.session_state[K_ERROR] = msg
                st.rerun()
                return

            ar = result.get("aspect_ratio", "1:1")
            num = result.get("num_images", 1)
            prompt_text = result.get("prompt", "")
            if len(prompt_text) > 10000:
                prompt_text = prompt_text[:10000]
            negative_prompt = result.get("negative_prompt", "")
            reference_image = result.get("reference_image", "")
            r_model_id = result.get("model_id", model_id)

            session = _find_session_or_create(r_model_id)

            source_images = []
            if session["turns"]:
                last_turn = session["turns"][-1]
                if last_turn.get("image_urls"):
                    source_images = list(last_turn["image_urls"])

            if not sidebar.test_mode:
                new_turn = {
                    "turn_id": result.get("item_id", f"{state_prefix}_{ts}"),
                    "prompt": prompt_text,
                    "model_id": r_model_id,
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
                pending_data = {
                    "session_id": session["id"],
                    "turn_id": new_turn["turn_id"],
                    "ar": ar, "num": num, "prompt": prompt_text,
                    "negative_prompt": negative_prompt,
                    "style_preset": result.get("style_preset"),
                }
                if reference_image:
                    pending_data["reference_image"] = reference_image
                if source_images:
                    pending_data["source_images"] = source_images
                st.session_state[K_PENDING] = pending_data
            else:
                new_turn = {
                    "turn_id": result.get("item_id", f"{state_prefix}_{ts}"),
                    "prompt": prompt_text,
                    "model_id": r_model_id,
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
                    upsert_nanobanana_session(cfg, st.session_state["user_id"], session, tab_id=tab_id)
                except Exception:
                    pass
            st.rerun()

        elif action == "new_session":
            new_id = str(uuid.uuid4())
            new_session = {"id": new_id, "title": "New Image", "model": model_id, "turns": []}
            st.session_state[K_SESSIONS].insert(0, new_session)
            st.session_state[K_ACTIVE] = new_id
            st.rerun()

        elif action == "switch_session":
            st.session_state[K_ACTIVE] = result.get("session_id")
            st.rerun()

        elif action == "delete_session":
            session_id = result.get("session_id")
            st.session_state[K_SESSIONS] = [
                s for s in st.session_state[K_SESSIONS] if s["id"] != session_id
            ]
            if _is_authenticated():
                try:
                    delete_nanobanana_session(cfg, st.session_state["user_id"], session_id)
                except Exception:
                    pass
            if st.session_state.get(K_ACTIVE) == session_id:
                if st.session_state[K_SESSIONS]:
                    st.session_state[K_ACTIVE] = st.session_state[K_SESSIONS][0]["id"]
                else:
                    st.session_state[K_ACTIVE] = ""
            st.rerun()

    return {
        "tab_id": tab_id,
        "title": title,
        "required_features": {feature_key},
        "render": render,
    }
