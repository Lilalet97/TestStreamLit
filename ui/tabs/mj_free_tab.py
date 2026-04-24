# ui/tabs/mj_free_tab.py
"""Midjourney Free 탭 — MJ 탭과 동일 기능, 크레딧 0."""
import re
import random
from pathlib import Path
from datetime import datetime, timezone, date

import streamlit as st
import streamlit.components.v1 as components

from core.config import AppConfig
from core.api_bridge import call_with_lease
from core.db import insert_mj_gallery_item, load_mj_gallery, update_mj_gallery_images, load_school_mj_gallery, load_nanobanana_sessions
from providers import google_imagen, useapi_mj
from ui.sidebar import SidebarState

# 동일한 HTML 컴포넌트 사용 (mj 템플릿)
_COMPONENT_DIR = Path(__file__).resolve().parent / "templates" / "mj"
_mj_free_component_func = components.declare_component("mj_free_component", path=str(_COMPONENT_DIR))

# ── 세션 키 (mj 탭과 분리) ──
_PREFIX = "mjf"
_K_GALLERY = f"{_PREFIX}_gallery"
_K_DB_LOADED = f"_{_PREFIX}_db_loaded"
_K_PROCESSED = f"_{_PREFIX}_processed_actions"
_K_PENDING = f"_{_PREFIX}_pending_submit"
_K_PENDING_DESCRIBE = f"_{_PREFIX}_pending_describe"
_K_ERROR = f"_{_PREFIX}_error_msg"
_K_CREDIT = f"_{_PREFIX}_credit_toast"
_K_GALLERY_OPEN = f"_{_PREFIX}_gallery_open"


def _mj_free_component(gallery_items, frame_height=900, key="mj_free_main",
                        enabled_features=None, school_gallery=None,
                        source_gallery=None, default_model=""):
    return _mj_free_component_func(
        gallery_items=gallery_items,
        frame_height=frame_height,
        enabled_features=enabled_features or [],
        school_gallery=school_gallery,
        source_gallery=source_gallery or [],
        default_model=default_model,
        key=key,
        default=None,
    )


# ── mj_tab.py 공용 함수들 임포트 ──
from ui.tabs.mj_tab import (
    _is_authenticated,
    _get_tab_features,
    _parse_prompt_params,
    _build_mj_full_text,
    _mock_image_urls,
    _VALID_AR,
)


def render_mj_free_tab(cfg: AppConfig, sidebar: SidebarState):
    """MJ Free 탭 렌더링 — 크레딧 0."""

    # DB 로드 (최초 1회)
    if not st.session_state.get(_K_DB_LOADED):
        if _is_authenticated():
            try:
                items = load_mj_gallery(cfg, st.session_state["user_id"])
                st.session_state[_K_GALLERY] = items
            except Exception:
                st.session_state[_K_GALLERY] = []
        else:
            st.session_state[_K_GALLERY] = []
        st.session_state[_K_DB_LOADED] = True

    # ── 대기 중인 생성 요청 처리 ──
    pending = st.session_state.get(_K_PENDING)
    if pending:
        del st.session_state[_K_PENDING]
        try:
            mj_prompt = _build_mj_full_text(pending["prompt"], pending.get("settings", {}))

            # 첨부 이미지 → GCS 업로드 → URL을 프롬프트 앞에 추가
            attached = pending.get("attached_images")
            if attached and cfg.gcs_bucket_name and cfg.vertex_sa_json:
                from providers.gcs_storage import upload_single_media_url
                img_urls_for_prompt = []
                for category in ["imagePrompts", "styleRef", "omniRef"]:
                    for data_url in (attached.get(category) or []):
                        try:
                            gcs_url = upload_single_media_url(
                                cfg.vertex_sa_json, cfg.gcs_bucket_name,
                                data_url, prefix="mj/refs",
                            )
                            if gcs_url and gcs_url.startswith("http"):
                                img_urls_for_prompt.append(gcs_url)
                        except Exception:
                            pass
                if img_urls_for_prompt:
                    mj_prompt = " ".join(img_urls_for_prompt) + " " + mj_prompt

            image_urls = call_with_lease(
                cfg,
                test_mode=sidebar.test_mode,
                provider="midjourney",
                mock_fn=lambda: _mock_image_urls(pending["aspect_ratio"], 4),
                real_fn=lambda kp: useapi_mj.imagine(
                    api_token=kp["api_key"],
                    prompt=mj_prompt,
                    channel=kp.get("channel", ""),
                ),
                model="mj_free",
            )

            # GCS 업로드
            if image_urls and cfg.gcs_bucket_name and cfg.vertex_sa_json:
                from providers.gcs_storage import upload_media_urls
                image_urls = upload_media_urls(
                    cfg.vertex_sa_json, cfg.gcs_bucket_name, image_urls, prefix="mj",
                )
        except Exception as e:
            st.session_state[_K_ERROR] = str(e)
            image_urls = []

        # 갤러리 아이템 업데이트
        loading_ts = pending.get("loading_ts")
        for item in st.session_state.get(_K_GALLERY, []):
            if item.get("loading") and item.get("loading_ts") == loading_ts:
                item["loading"] = False
                item.pop("loading_ts", None)
                if image_urls:
                    item["images"] = image_urls
                    if _is_authenticated() and item.get("id"):
                        try:
                            update_mj_gallery_images(cfg, item["id"], image_urls)
                        except Exception:
                            pass
                break

        # 크레딧 차감 없음 (mj_free)
        st.rerun()

    # ── 대기 중인 Describe 요청 처리 ──
    describe_pending = st.session_state.get(_K_PENDING_DESCRIBE)
    if describe_pending:
        del st.session_state[_K_PENDING_DESCRIBE]
        try:
            image_data_url = describe_pending["image_data_url"]
            from providers.gcs_storage import upload_single_media_url
            gcs_url = upload_single_media_url(
                cfg.vertex_sa_json, cfg.gcs_bucket_name,
                image_data_url, prefix="mj/describe",
            )
            if not gcs_url or not gcs_url.startswith("http"):
                raise RuntimeError("이미지 업로드 실패")

            prompts = call_with_lease(
                cfg,
                test_mode=sidebar.test_mode,
                provider="midjourney",
                mock_fn=lambda: [
                    "a beautiful landscape with mountains and rivers",
                    "scenic view of nature with vibrant colors",
                    "panoramic mountain scenery at golden hour",
                    "serene natural landscape photography",
                ],
                real_fn=lambda kp: useapi_mj.describe(
                    api_token=kp["api_key"],
                    image_url=gcs_url,
                    channel=kp.get("channel", ""),
                ),
                model="describe",
            )

            describe_item = {
                "date": date.today().isoformat(),
                "prompt": "\n".join(f"{i+1}. {p}" for i, p in enumerate(prompts)),
                "tags": ["describe"],
                "aspect_ratio": "",
                "images": [gcs_url],
                "settings": {},
            }
            if _is_authenticated():
                try:
                    row_id = insert_mj_gallery_item(cfg, st.session_state["user_id"], describe_item)
                    describe_item["id"] = row_id
                except Exception:
                    pass
            st.session_state[_K_GALLERY].insert(0, describe_item)
            # 크레딧 차감 없음
        except Exception as e:
            st.session_state[_K_ERROR] = f"Describe 오류: {e}"
        st.rerun()

    # ── 에러/크레딧 메시지 ──
    _err = st.session_state.pop(_K_ERROR, None)
    if _err:
        st.toast(_err, icon="⚠️")
        from core.db import insert_error_log
        insert_error_log(cfg, st.session_state.get("user_id", ""), st.session_state.get("school_id", "default"), "mj_free", _err)

    _cred = st.session_state.pop(_K_CREDIT, None)
    if _cred is not None:
        st.toast(f"크레딧 차감 없음 (Free 모드)", icon="🆓")

    # Streamlit 패딩 제거 + iframe 전체 화면
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
    if st.session_state.get(_K_GALLERY_OPEN):
        school_id = st.session_state.get("school_id", "default")
        school_gallery = load_school_mj_gallery(cfg, school_id)

    # 갤러리 피커용: MJ + NanoBanana 이미지
    nano_gallery = []
    if _is_authenticated():
        try:
            mj_items = load_mj_gallery(cfg, st.session_state["user_id"], limit=20)
            for item in mj_items:
                for url in (item.get("images") or []):
                    if url:
                        nano_gallery.append({
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
                            nano_gallery.append({
                                "source": "nanobanana",
                                "prompt": prompt,
                                "url": url,
                            })
        except Exception:
            pass

    result = _mj_free_component(
        gallery_items=st.session_state.get(_K_GALLERY, []),
        frame_height=900,
        enabled_features=_get_tab_features(cfg, "mj."),
        school_gallery=school_gallery,
        source_gallery=nano_gallery,
        default_model=cfg.google_imagen_model,
    )

    if not result or not isinstance(result, dict):
        return

    action = result.get("action")
    ts = result.get("ts", 0)
    _item_id = result.get("item_id", "")
    _loading_ts = result.get("loading_ts", "")
    dedup_key = f"{action}_{ts}_{_item_id}_{_loading_ts}"
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
    elif action == "describe":
        if not _is_authenticated():
            return
        if st.session_state.get(_K_PENDING_DESCRIBE):
            return
        # 크레딧 확인 불필요 (Free)
        image_data = result.get("describe_image", "")
        if not image_data:
            return
        st.session_state[_K_PENDING_DESCRIBE] = {
            "image_data_url": image_data,
        }
        st.rerun()
    elif action == "submit":
        if not _is_authenticated():
            return
        if st.session_state.get(_K_PENDING):
            return

        # 크레딧 확인 불필요 (Free)

        raw_prompt = result.get("prompt", "")
        if len(raw_prompt) > 10000:
            raw_prompt = raw_prompt[:10000]
        s = result.get("settings", {})
        prompt, s = _parse_prompt_params(raw_prompt, s)

        ar = s.get("aspectRatio", "1:1")
        if ar not in _VALID_AR:
            ar = "1:1"

        tags = []
        if ar != "1:1":
            tags.append(f"ar {ar}")
        if s.get("mode") == "Raw":
            tags.append("raw")
        stylization = int(s.get("stylization", 100))
        if stylization != 100:
            tags.append(f"s {stylization}")
        weirdness = int(s.get("weirdness", 0))
        if weirdness:
            tags.append(f"w {weirdness}")
        variety = int(s.get("variety", 0))
        if variety:
            tags.append(f"c {variety}")
        tags.append("relax")

        today = date.today().isoformat()

        if not sidebar.test_mode:
            new_item = {
                "date": today,
                "prompt": prompt,
                "tags": tags,
                "aspect_ratio": ar,
                "images": [],
                "attached_images": result.get("attachedImages"),
                "loading": True,
                "loading_ts": ts,
            }

            if _is_authenticated():
                try:
                    db_item = dict(new_item)
                    db_item["settings"] = {
                        k: v for k, v in s.items()
                        if k not in ("stealth", "videoRes", "videoBatch")
                    }
                    row_id = insert_mj_gallery_item(
                        cfg, st.session_state["user_id"], db_item,
                    )
                    new_item["id"] = row_id
                except Exception:
                    pass

            st.session_state.setdefault(_K_GALLERY, []).insert(0, new_item)

            st.session_state[_K_PENDING] = {
                "prompt": prompt,
                "settings": s,
                "attached_images": result.get("attachedImages"),
                "aspect_ratio": ar,
                "loading_ts": ts,
            }
        else:
            new_item = {
                "date": today,
                "prompt": prompt,
                "tags": tags,
                "aspect_ratio": ar,
                "images": [],
                "attached_images": result.get("attachedImages"),
                "loading": True,
                "loading_ts": ts,
            }

            if _is_authenticated():
                try:
                    db_item = dict(new_item)
                    db_item["settings"] = {
                        k: v for k, v in s.items()
                        if k not in ("stealth", "videoRes", "videoBatch")
                    }
                    row_id = insert_mj_gallery_item(
                        cfg, st.session_state["user_id"], db_item,
                    )
                    new_item["id"] = row_id
                except Exception:
                    pass

            st.session_state.setdefault(_K_GALLERY, []).insert(0, new_item)

        st.rerun()

    elif action == "loading_complete":
        loading_ts = result.get("loading_ts")
        mock_images = result.get("mock_images", [])
        updated = False
        for item in st.session_state.get(_K_GALLERY, []):
            if item.get("loading") and item.get("loading_ts") == loading_ts:
                item["loading"] = False
                item.pop("loading_ts", None)
                if mock_images:
                    item["images"] = mock_images
                    if _is_authenticated() and item.get("id"):
                        try:
                            update_mj_gallery_images(cfg, item["id"], mock_images)
                        except Exception:
                            pass
                updated = True
                break
        if updated:
            st.rerun()


TAB = {
    "tab_id": "mj_free",
    "title": "Image Create",
    "required_features": {"tab.mj_free"},
    "render": render_mj_free_tab,
}
