# ui/tabs/gallery_tab.py
"""갤러리 탭 — 내 작업 / 학교 갤러리 서브탭."""
import json
import streamlit as st

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
    load_gpt_conversations,
)
from ui.sidebar import SidebarState


def _safe_json(val):
    if isinstance(val, str):
        try:
            return json.loads(val)
        except Exception:
            return []
    return val if val else []


def _render_mj_items(items: list):
    """MJ 갤러리 아이템을 그리드로 렌더링."""
    if not items:
        st.caption("Midjourney 작업이 없습니다.")
        return
    for item in items:
        images = _safe_json(item.get("images") or item.get("images_json"))
        valid_images = [u for u in images if u and not u.startswith("./")]
        prompt = item.get("prompt", "")[:80]
        date = item.get("display_date") or item.get("created_at", "")[:10]
        label = f"**MJ** · {prompt}..." if len(item.get("prompt", "")) > 80 else f"**MJ** · {prompt}"
        if date:
            label += f" · {date}"
        st.caption(label)
        if valid_images:
            img_cols = st.columns(len(valid_images))
            for j, img_url in enumerate(valid_images):
                with img_cols[j]:
                    st.image(img_url, width="stretch")
        else:
            st.markdown(
                '<div style="background:var(--secondary-background-color);'
                'border-radius:8px;padding:40px;text-align:center;color:gray;">🎨 생성 중</div>',
                unsafe_allow_html=True,
            )
        st.divider()


def _render_kling_items(items: list):
    """Kling 비디오 아이템 렌더링."""
    if not items:
        st.caption("Kling 작업이 없습니다.")
        return
    for item in items:
        video_urls = _safe_json(item.get("video_urls") or item.get("video_urls_json"))
        valid_videos = [u for u in video_urls if u]
        prompt = item.get("prompt", "")[:80]
        st.caption(f"**Kling** · {prompt}")
        if valid_videos:
            vid_cols = st.columns(len(valid_videos))
            for j, vid_url in enumerate(valid_videos):
                with vid_cols[j]:
                    st.video(vid_url)
        else:
            st.markdown(
                '<div style="background:var(--secondary-background-color);'
                'border-radius:8px;padding:40px;text-align:center;color:gray;">🎬 대기 중</div>',
                unsafe_allow_html=True,
            )
        st.divider()


def _render_elevenlabs_items(items: list):
    """ElevenLabs 오디오 아이템 렌더링."""
    if not items:
        st.caption("ElevenLabs 작업이 없습니다.")
        return
    for item in items:
        audio_url = item.get("audio_url", "")
        text = item.get("text", "")[:100]
        voice = item.get("voice_name", "알 수 없음")
        col1, col2 = st.columns([1, 3])
        with col1:
            st.caption(f"🔊 **{voice}**")
        with col2:
            if audio_url and audio_url.startswith(("http://", "https://", "data:")):
                st.audio(audio_url)
            st.caption(text)


def _render_nanobanana_items(items: list):
    """NanoBanana 이미지 아이템 렌더링."""
    if not items:
        st.caption("NanoBanana 작업이 없습니다.")
        return
    for item in items:
        image_urls = _safe_json(item.get("image_urls") or item.get("image_urls_json"))
        valid_images = [u for u in image_urls if u]
        prompt = item.get("prompt", "")[:80]
        st.caption(f"**NB** · {prompt}")
        if valid_images:
            img_cols = st.columns(len(valid_images))
            for j, img_url in enumerate(valid_images):
                with img_cols[j]:
                    st.image(img_url, width="stretch")
        else:
            st.markdown(
                '<div style="background:var(--secondary-background-color);'
                'border-radius:8px;padding:40px;text-align:center;color:gray;">🖼️</div>',
                unsafe_allow_html=True,
            )
        st.divider()


def _render_gpt_items(items: list):
    """GPT 대화 목록 렌더링."""
    if not items:
        st.caption("GPT 대화가 없습니다.")
        return
    for item in items:
        title = item.get("title", "제목 없음")
        model = item.get("model", "")
        messages = _safe_json(item.get("messages") or item.get("messages_json"))
        msg_count = len(messages) if messages else 0
        date = item.get("updated_at", "")[:10]
        st.markdown(
            f"💬 **{title}** · {model} · {msg_count}개 메시지 · {date}"
        )


def render_gallery_tab(cfg: AppConfig, sidebar: SidebarState):
    """갤러리 탭 메인 렌더 함수."""
    user_id = st.session_state.get("user_id", "guest")
    school_id = st.session_state.get("school_id", "default")
    features = set(cfg.get_enabled_features(school_id))

    # 활성화된 탭에 따라 갤러리 섹션 결정
    has_gpt = "tab.gpt" in features
    has_mj = "tab.mj" in features
    has_nb = any(f.startswith("tab.nanobanana") for f in features)
    has_kling = any(f.startswith("tab.kling") for f in features)
    has_el = "tab.elevenlabs" in features

    st.markdown(
        """<style>
        .stMainBlockContainer {max-width:1200px !important;}
        </style>""",
        unsafe_allow_html=True,
    )

    tab_my, tab_school = st.tabs(["📁 내 작업", "🏫 학교 갤러리"])

    # ── 내 작업 ──
    with tab_my:
        if user_id == "guest":
            st.info("로그인 후 이용 가능합니다.")
            return

        # 필터 옵션: 열린 탭만
        my_options = ["전체"]
        if has_gpt: my_options.append("GPT")
        if has_mj: my_options.append("Midjourney")
        if has_nb: my_options.append("NanoBanana")
        if has_kling: my_options.append("Kling")
        if has_el: my_options.append("ElevenLabs")

        filter_provider = st.selectbox("필터", my_options, key="gallery_my_filter")

        if has_gpt and filter_provider in ("전체", "GPT"):
            with st.expander("💬 GPT 대화", expanded=(filter_provider == "GPT")):
                gpt_items = load_gpt_conversations(cfg, user_id, limit=30)
                _render_gpt_items(gpt_items)

        if has_mj and filter_provider in ("전체", "Midjourney"):
            with st.expander("🎨 Midjourney", expanded=(filter_provider == "Midjourney")):
                mj_items = load_mj_gallery(cfg, user_id, limit=30)
                _render_mj_items(mj_items)

        if has_nb and filter_provider in ("전체", "NanoBanana"):
            with st.expander("🖼️ NanoBanana", expanded=(filter_provider == "NanoBanana")):
                nb_items = load_nanobanana_history(cfg, user_id, limit=30)
                _render_nanobanana_items(nb_items)

        if has_kling and filter_provider in ("전체", "Kling"):
            with st.expander("🎬 Kling", expanded=(filter_provider == "Kling")):
                kling_items = load_kling_web_history(cfg, user_id, limit=30)
                _render_kling_items(kling_items)

        if has_el and filter_provider in ("전체", "ElevenLabs"):
            with st.expander("🔊 ElevenLabs", expanded=(filter_provider == "ElevenLabs")):
                el_items = load_elevenlabs_history(cfg, user_id, limit=30)
                _render_elevenlabs_items(el_items)

    # ── 학교 갤러리 ──
    with tab_school:
        school_options = ["전체"]
        if has_mj: school_options.append("Midjourney")
        if has_nb: school_options.append("NanoBanana")
        if has_kling: school_options.append("Kling")
        if has_el: school_options.append("ElevenLabs")

        filter_school = st.selectbox("필터", school_options, key="gallery_school_filter")

        if has_mj and filter_school in ("전체", "Midjourney"):
            with st.expander("🎨 Midjourney", expanded=(filter_school == "Midjourney")):
                items = load_school_mj_gallery(cfg, school_id, limit=50)
                _render_mj_items(items)

        if has_nb and filter_school in ("전체", "NanoBanana"):
            with st.expander("🖼️ NanoBanana", expanded=(filter_school == "NanoBanana")):
                items = load_school_nanobanana_gallery(cfg, school_id, limit=50)
                _render_nanobanana_items(items)

        if has_kling and filter_school in ("전체", "Kling"):
            with st.expander("🎬 Kling", expanded=(filter_school == "Kling")):
                items = load_school_kling_gallery(cfg, school_id, limit=50)
                _render_kling_items(items)

        if has_el and filter_school in ("전체", "ElevenLabs"):
            with st.expander("🔊 ElevenLabs", expanded=(filter_school == "ElevenLabs")):
                items = load_school_elevenlabs_gallery(cfg, school_id, limit=50)
                _render_elevenlabs_items(items)


TAB = {
    "tab_id": "gallery",
    "title": "🖼️ Gallery",
    "required_features": set(),  # 항상 노출 (feature flag 불필요)
    "render": render_gallery_tab,
}
