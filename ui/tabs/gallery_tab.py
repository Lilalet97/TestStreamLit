# ui/tabs/gallery_tab.py
"""갤러리 탭 — HTML 컴포넌트 기반 (탭/필터/라이트박스 전부 JS)."""
import json
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from core.config import AppConfig
from core.db import (
    load_mj_gallery,
    load_school_mj_gallery,
    load_kling_web_history,
    load_school_kling_gallery,
    load_elevenlabs_history,
    load_school_elevenlabs_gallery,
    load_nanobanana_history,
    load_school_nanobanana_gallery,
)
from ui.sidebar import SidebarState

_COMP_DIR = Path(__file__).resolve().parent.parent / "components" / "gallery_grid"
_gallery_component = components.declare_component("gallery_grid", path=str(_COMP_DIR))


def _safe_json(val):
    if isinstance(val, str):
        try:
            return json.loads(val)
        except Exception:
            return []
    return val if val else []


def _build_items(provider: str, raw_items: list) -> list:
    """DB raw 데이터 → 컴포넌트용 items 리스트."""
    items = []
    for item in raw_items:
        if provider == "Midjourney":
            images = _safe_json(item.get("images") or item.get("images_json"))
            urls = [u for u in images if u and not u.startswith("./")]
            if not urls:
                continue
            prompt = item.get("prompt", "")
            date = item.get("display_date") or item.get("created_at", "")[:10]
            items.append({
                "type": "image", "urls": urls, "prompt": prompt,
                "label": f"MJ · {prompt[:80]}{'...' if len(prompt)>80 else ''}" + (f" · {date}" if date else ""),
                "nickname": item.get("nickname", ""), "item_type": "Midjourney",
                "date": date, "ar": item.get("aspect_ratio", ""),
            })
        elif provider == "NanoBanana":
            image_urls = _safe_json(item.get("image_urls") or item.get("image_urls_json"))
            urls = [u for u in image_urls if u]
            if not urls:
                continue
            prompt = item.get("prompt", "")
            items.append({
                "type": "image", "urls": urls, "prompt": prompt,
                "label": f"NB · {prompt[:80]}",
                "nickname": item.get("nickname", ""), "item_type": "NanoBanana",
                "date": item.get("created_at", "")[:10], "model": item.get("model_label", ""),
                "ar": item.get("aspect_ratio", ""),
            })
        elif provider == "Kling":
            video_urls = _safe_json(item.get("video_urls") or item.get("video_urls_json"))
            urls = [u for u in video_urls if u]
            prompt = item.get("prompt", "")
            items.append({
                "type": "video", "urls": urls, "prompt": prompt,
                "label": f"Kling · {prompt[:80]}",
                "nickname": item.get("nickname", ""), "item_type": "Kling",
                "date": item.get("created_at", "")[:10], "model": item.get("model_label", ""),
            })
        elif provider == "ElevenLabs":
            audio_url = item.get("audio_url", "")
            if not audio_url:
                continue
            items.append({
                "type": "audio", "audio_url": audio_url,
                "prompt": item.get("text", ""),
                "label": f"ElevenLabs · {item.get('voice_name', '')}",
                "voice": item.get("voice_name", ""),
                "nickname": item.get("nickname", ""), "item_type": "ElevenLabs",
                "date": item.get("created_at", "")[:10], "model": item.get("model_label", ""),
            })
    return items


def render_gallery_tab(cfg: AppConfig, sidebar: SidebarState):
    """갤러리 탭 — 다른 탭과 동일한 전체화면 iframe 구조."""
    user_id = st.session_state.get("user_id", "guest")
    school_id = st.session_state.get("school_id", "default")
    features = set(cfg.get_enabled_features(school_id))

    has_mj = "tab.mj" in features
    has_nb = any(f.startswith("tab.nanobanana") for f in features)
    has_kling = any(f.startswith("tab.kling") for f in features)
    has_el = "tab.elevenlabs" in features

    providers = []
    if has_mj: providers.append("Midjourney")
    if has_nb: providers.append("NanoBanana")
    if has_kling: providers.append("Kling")
    if has_el: providers.append("ElevenLabs")

    # 모든 데이터 미리 로드
    all_data = {}
    for prov in providers:
        my_loader = {
            "Midjourney": lambda: load_mj_gallery(cfg, user_id, limit=30),
            "NanoBanana": lambda: load_nanobanana_history(cfg, user_id, limit=30),
            "Kling": lambda: load_kling_web_history(cfg, user_id, limit=30),
            "ElevenLabs": lambda: load_elevenlabs_history(cfg, user_id, limit=30),
        }
        school_loader = {
            "Midjourney": lambda: load_school_mj_gallery(cfg, school_id, limit=50),
            "NanoBanana": lambda: load_school_nanobanana_gallery(cfg, school_id, limit=50),
            "Kling": lambda: load_school_kling_gallery(cfg, school_id, limit=50),
            "ElevenLabs": lambda: load_school_elevenlabs_gallery(cfg, school_id, limit=50),
        }
        my_items = _build_items(prov, my_loader[prov]()) if user_id != "guest" else []
        school_items = _build_items(prov, school_loader[prov]())
        all_data[prov] = {"my": my_items, "school": school_items}

    # 다른 탭과 동일한 CSS
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

    _gallery_component(
        all_data=all_data,
        providers=providers,
        is_guest=(user_id == "guest"),
        key="gallery_grid_main",
    )


TAB = {
    "tab_id": "gallery",
    "title": "Gallery",
    "required_features": set(),
    "render": render_gallery_tab,
}
