# ui/tabs/mj_tab.py
"""Midjourney /imagine 페이지 — declare_component 양방향 통신."""
import re
import random
from pathlib import Path
from datetime import datetime, timezone

import streamlit as st
import streamlit.components.v1 as components

from core.config import AppConfig
from core.api_bridge import call_with_lease
from core.db import insert_mj_gallery_item, load_mj_gallery, update_mj_gallery_images, load_school_mj_gallery, load_nanobanana_sessions
from providers import google_imagen, useapi_mj
from ui.sidebar import SidebarState

_COMPONENT_DIR = Path(__file__).resolve().parent / "templates" / "mj"
_mj_component_func = components.declare_component("mj_component", path=str(_COMPONENT_DIR))


def _mj_component(gallery_items: list, frame_height: int = 900, key: str = "mj_main",
                   enabled_features: list | None = None, school_gallery: list | None = None,
                   source_gallery: list | None = None, default_model: str = ""):
    """MJ 커스텀 컴포넌트 래퍼. 반환값: JS에서 setComponentValue로 보낸 dict 또는 None."""
    return _mj_component_func(
        gallery_items=gallery_items,
        frame_height=frame_height,
        enabled_features=enabled_features or [],
        school_gallery=school_gallery,
        source_gallery=source_gallery or [],
        default_model=default_model,
        key=key,
        default=None,
    )


def _is_authenticated() -> bool:
    return (
        st.session_state.get("auth_logged_in", False)
        and st.session_state.get("user_id", "guest") != "guest"
    )


def _init_state(cfg: AppConfig):
    """세션 상태 초기화: 로그인 사용자는 DB에서 로드."""
    if "mj_gallery" in st.session_state and st.session_state.get("_mj_db_loaded"):
        return

    if _is_authenticated():
        items = load_mj_gallery(cfg, st.session_state["user_id"])
        if items:
            st.session_state.mj_gallery = items
            st.session_state["_mj_db_loaded"] = True
            return

    # 비로그인(게스트) 또는 DB에 데이터 없음 → 빈 갤러리
    if "mj_gallery" not in st.session_state:
        st.session_state.mj_gallery = []
    st.session_state["_mj_db_loaded"] = True


_VALID_AR = {
    "1:2", "6:11", "9:16", "2:3", "3:4", "4:5", "5:6",
    "1:1",
    "6:5", "5:4", "4:3", "3:2", "16:9", "2:1", "21:9",
}
_VALID_VERSIONS = {"5", "5.1", "5.2", "6", "6.1", "7"}


def _strip(prompt: str, m: re.Match) -> str:
    """매치된 부분을 프롬프트에서 제거."""
    return prompt[:m.start()] + prompt[m.end():]


def _parse_prompt_params(prompt: str, settings: dict) -> tuple[str, dict]:
    """프롬프트에서 MJ 스타일 파라미터(--ar, --s 등)를 추출하여 settings에 병합.

    값이 유효 범위를 벗어나면 무시하고 프롬프트에 그대로 남긴다.

    Returns:
        (clean_prompt, merged_settings)
    """
    s = dict(settings)

    # --ar W:H (aspect ratio) — 설정 패널 AR_LIST 목록만 허용
    m = re.search(r'--ar\s+(\d+:\d+)', prompt)
    if m:
        ar_val = m.group(1)
        if ar_val in _VALID_AR:
            s["aspectRatio"] = ar_val
            prompt = _strip(prompt, m)

    # --s / --stylize N — 범위 0~1000
    m = re.search(r'--(?:s|stylize)\s+(\d+)', prompt)
    if m:
        v = int(m.group(1))
        if 0 <= v <= 1000:
            s["stylization"] = v
            prompt = _strip(prompt, m)

    # --w / --weird N — 범위 0~3000
    m = re.search(r'--(?:w|weird)\s+(\d+)', prompt)
    if m:
        v = int(m.group(1))
        if 0 <= v <= 3000:
            s["weirdness"] = v
            prompt = _strip(prompt, m)

    # --c / --chaos N — 범위 0~100
    m = re.search(r'--(?:c|chaos)\s+(\d+)', prompt)
    if m:
        v = int(m.group(1))
        if 0 <= v <= 100:
            s["variety"] = v
            prompt = _strip(prompt, m)

    # --v N (version) — 허용: 5, 5.1, 5.2, 6, 6.1, 7
    m = re.search(r'--v\s+([\d.]+)', prompt)
    if m:
        ver = m.group(1)
        if ver in _VALID_VERSIONS:
            s["version"] = ver
            prompt = _strip(prompt, m)

    # --style raw / --style standard (mode)
    m = re.search(r'--style\s+(raw|standard)\b', prompt)
    if m:
        s["mode"] = "Raw" if m.group(1) == "raw" else "Standard"
        prompt = _strip(prompt, m)

    # --turbo / --relax / --fast (speed)
    for flag, speed in [("turbo", "Turbo"), ("relax", "Relax"), ("fast", "Fast")]:
        m = re.search(rf'--{flag}\b', prompt)
        if m:
            s["speed"] = speed
            prompt = _strip(prompt, m)

    # UI 전용 필드 제거 (태그/저장 불필요)
    for k in ("stealth", "videoRes", "videoBatch"):
        s.pop(k, None)

    # 정리: 연속 공백/쉼표 정리, 양쪽 공백 제거
    prompt = re.sub(r'\s{2,}', ' ', prompt).strip().rstrip(',').strip()

    return prompt, s


def _build_tags(s: dict) -> list:
    """설정으로부터 태그 리스트 생성."""
    tags = []
    ar = s.get("aspectRatio", "1:1")
    if ar != "1:1":
        tags.append(f"ar {ar}")
    if s.get("mode") == "Raw":
        tags.append("raw")
    if int(s.get("stylization", 100)) != 100:
        tags.append(f"s {s['stylization']}")
    if int(s.get("weirdness", 0)) != 0:
        tags.append(f"w {s['weirdness']}")
    if int(s.get("variety", 0)) != 0:
        tags.append(f"c {s['variety']}")
    v = s.get("version", "7")
    if v != "7":
        tags.append(f"v {v}")
    if s.get("speed") == "Turbo":
        tags.append("turbo")
    elif s.get("speed") == "Relax":
        tags.append("relax")
    return tags


def _build_mj_full_text(prompt: str, settings: dict) -> str:
    """프롬프트 + 설정 → /imagine 형식의 전체 텍스트 조합."""
    parts = [prompt]
    ar = settings.get("aspectRatio", "1:1")
    if ar != "1:1":
        parts.append(f"--ar {ar}")
    if settings.get("mode") == "Raw":
        parts.append("--style raw")
    stylization = int(settings.get("stylization", 100))
    if stylization != 100:
        parts.append(f"--s {stylization}")
    weirdness = int(settings.get("weirdness", 0))
    if weirdness:
        parts.append(f"--w {weirdness}")
    variety = int(settings.get("variety", 0))
    if variety:
        parts.append(f"--c {variety}")
    ver = settings.get("version", "7")
    if ver != "7":
        parts.append(f"--v {ver}")
    # Fast/Turbo 비활성화 — 무조건 Relax로 강제
    parts.append("--relax")
    return " ".join(parts)


# ── Gemini 기반 이미지 생성 (Google AI Studio) ──────────────────

_GEMINI_AR = {"1:1": 1.0, "16:9": 16/9, "9:16": 9/16, "4:3": 4/3, "3:4": 3/4}
_ASPECT_SIZES = {
    "1:1": (1024, 1024), "16:9": (1024, 576), "9:16": (576, 1024),
    "4:3": (1024, 768), "3:4": (768, 1024),
}


def _map_aspect_ratio(ar: str) -> str:
    """MJ의 15개 비율 → Gemini 지원 5개 중 가장 가까운 것으로 매핑."""
    parts = ar.split(":")
    if len(parts) != 2:
        return "1:1"
    try:
        ratio = int(parts[0]) / int(parts[1])
    except (ValueError, ZeroDivisionError):
        return "1:1"
    best, best_diff = "1:1", float("inf")
    for k, v in _GEMINI_AR.items():
        diff = abs(ratio - v)
        if diff < best_diff:
            best, best_diff = k, diff
    return best


def _build_enhanced_prompt(prompt: str, settings: dict) -> str:
    """MJ 세팅을 자연어 수식어로 변환하여 프롬프트에 추가."""
    modifiers = []
    stylization = int(settings.get("stylization", 100))
    if stylization > 500:
        modifiers.append("highly artistic and stylized")
    elif stylization > 300:
        modifiers.append("artistic")

    weirdness = int(settings.get("weirdness", 0))
    if weirdness > 500:
        modifiers.append("creative and unusual")
    elif weirdness > 200:
        modifiers.append("slightly unconventional")

    variety = int(settings.get("variety", 0))
    if variety > 50:
        modifiers.append("diverse and varied")

    if settings.get("mode") == "Raw":
        modifiers.append("raw, unprocessed, photographic look")

    if not modifiers:
        return prompt
    return f"{prompt}, {', '.join(modifiers)}"


def _generate_images(
    api_key: str,
    prompt: str, settings: dict,
    attached_images: dict | None, aspect_ratio: str, num_images: int = 4,
    model: str = "",
    # [VERTEX AI] sa_json: str = "", project_id: str = "", location: str = "",
) -> list[str]:
    """MJ 요청을 Gemini generateContent로 변환하여 호출."""
    mapped_ar = _map_aspect_ratio(aspect_ratio)
    enhanced = _build_enhanced_prompt(prompt, settings)

    parts: list = [{"text": enhanced}]

    if attached_images:
        img_prompts = attached_images.get("imagePrompts", [])
        style_refs = attached_images.get("styleRef", [])
        omni_refs = attached_images.get("omniRef", [])

        if img_prompts:
            parts.append({"text": "Use these images as the base/starting point for generation:"})
            parts.extend(img_prompts)
        if style_refs:
            parts.append({"text": "Match the visual style and aesthetic of these reference images:"})
            parts.extend(style_refs)
        if omni_refs:
            parts.append({"text": "Maintain the likeness and identity of the person/object in these images:"})
            parts.extend(omni_refs)

    return google_imagen.gemini_generate(
        api_key=api_key,
        parts=parts, aspect_ratio=mapped_ar, num_images=num_images,
        model=model or google_imagen.EDIT_MODEL,
        # [VERTEX AI] sa_json=sa_json, project_id=project_id, location=location,
    )


def _mock_image_urls(aspect_ratio: str, num_images: int) -> list[str]:
    """picsum.photos 기반 mock 이미지 URL 생성."""
    mapped = _map_aspect_ratio(aspect_ratio)
    w, h = _ASPECT_SIZES.get(mapped, (1024, 1024))
    return [
        f"https://picsum.photos/seed/mj{random.randint(1, 99999)}/{w}/{h}"
        for _ in range(num_images)
    ]


def _get_tab_features(cfg: AppConfig, prefix: str) -> list:
    """현재 학교의 enabled_features 중 해당 탭 prefix만 필터."""
    school_id = st.session_state.get("school_id", "default")
    return [f for f in cfg.get_enabled_features(school_id) if f.startswith(prefix)]


def render_mj_tab(cfg: AppConfig, sidebar: SidebarState):
    """Midjourney 탭: declare_component 양방향 통신."""
    _init_state(cfg)

    # ── 대기 중인 생성 요청 처리 (2단계: 실제 API 호출) ──
    pending = st.session_state.get("_mj_pending_submit")
    if pending:
        del st.session_state["_mj_pending_submit"]
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
                test_mode=False,
                provider="midjourney",
                mock_fn=lambda: _mock_image_urls(pending["aspect_ratio"], 4),
                real_fn=lambda kp: useapi_mj.imagine(
                    api_token=kp["api_key"],
                    prompt=mj_prompt,
                    channel=kp.get("channel", ""),
                ),
            )
            # GCS 업로드 (설정 시)
            if image_urls and cfg.gcs_bucket_name and cfg.vertex_sa_json:
                from providers.gcs_storage import upload_media_urls
                image_urls = upload_media_urls(
                    cfg.vertex_sa_json, cfg.gcs_bucket_name, image_urls, prefix="mj",
                )
        except Exception as e:
            image_urls = []
            st.session_state["_mj_error_msg"] = f"MJ API 오류: {e}"

        # ── 크레딧 차감 (Phase 2) ──
        if image_urls:
            from core.credits import deduct_after_success
            try:
                # Relax 고정: 이미지당 2크레딧 × 4장 = 8
                _cost = 2 * 4
                new_bal = deduct_after_success(cfg, _cost, tab_id="mj")
                if new_bal >= 0:
                    st.session_state["_mj_credit_toast"] = new_bal
            except Exception:
                pass

        # 로딩 아이템 업데이트
        for item in st.session_state.get("mj_gallery", []):
            if item.get("loading") and item.get("loading_ts") == pending["loading_ts"]:
                item["images"] = image_urls
                item["loading"] = False
                item.pop("loading_ts", None)
                if _is_authenticated() and item.get("id") and image_urls:
                    try:
                        update_mj_gallery_images(cfg, item["id"], image_urls)
                    except Exception:
                        pass
                break
        st.rerun()

    # ── 대기 중인 Describe 요청 처리 ──
    describe_pending = st.session_state.get("_mj_pending_describe")
    if describe_pending:
        del st.session_state["_mj_pending_describe"]
        try:
            image_data_url = describe_pending["image_data_url"]
            # GCS에 업로드하여 공개 URL 획득
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
            # DB에 Describe 기록 저장
            from datetime import date
            describe_item = {
                "date": date.today().isoformat(),
                "prompt": "\n".join(f"{i+1}. {p}" for i, p in enumerate(prompts)),
                "tags": ["describe"],
                "aspect_ratio": "",
                "images": [gcs_url],  # 분석한 원본 이미지
                "settings": {},
            }
            if _is_authenticated():
                try:
                    row_id = insert_mj_gallery_item(cfg, st.session_state["user_id"], describe_item)
                    describe_item["id"] = row_id
                except Exception:
                    pass
            st.session_state.mj_gallery.insert(0, describe_item)

            # Phase 2: 크레딧 차감 (1크레딧)
            from core.credits import deduct_after_success
            from core.credits import get_feature_cost as _gfc
            new_bal = deduct_after_success(cfg, _gfc(cfg, "mj_describe"), tab_id="mj_describe")
            if new_bal is not None:
                st.session_state["_mj_credit_toast"] = new_bal
        except Exception as e:
            st.session_state["_mj_error_msg"] = f"Describe 오류: {e}"
        st.rerun()

    # ── 에러 메시지 표시 (이전 rerun에서 저장된 것) ──
    _err = st.session_state.pop("_mj_error_msg", None)
    if _err:
        st.toast(_err, icon="⚠️")
        from core.db import insert_error_log
        insert_error_log(cfg, st.session_state.get("user_id", ""), st.session_state.get("school_id", "default"), "midjourney", _err)

    _cred = st.session_state.pop("_mj_credit_toast", None)
    if _cred is not None:
        st.toast(f"크레딧 차감 완료 (잔여: {_cred})", icon="💰")

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

    # 학교 공유 갤러리 데이터 로드
    school_gallery = None
    if st.session_state.get("_mj_gallery_open"):
        school_id = st.session_state.get("school_id", "default")
        school_gallery = load_school_mj_gallery(cfg, school_id)

    # 갤러리 피커용: NanoBanana 이미지 로드 (sessions.turns_json에서 추출)
    nano_gallery = []
    if _is_authenticated():
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

    # 컴포넌트 렌더링 — gallery_items를 JS에 전달하고, JS에서 보낸 값을 반환받음
    result = _mj_component(
        gallery_items=st.session_state.mj_gallery,
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

    # 중복 실행 방지: 처리 완료된 action key set으로 체크
    _item_id = result.get("item_id", "")
    _loading_ts = result.get("loading_ts", "")
    dedup_key = f"{action}_{ts}_{_item_id}_{_loading_ts}"
    _processed = st.session_state.setdefault("_mj_processed_actions", set())
    if dedup_key in _processed:
        return
    _processed.add(dedup_key)
    if len(_processed) > 500:
        st.session_state["_mj_processed_actions"] = {dedup_key}

    if action == "open_gallery":
        st.session_state["_mj_gallery_open"] = True
        st.rerun()
    elif action == "close_gallery":
        st.session_state["_mj_gallery_open"] = False
        st.rerun()
    elif action == "describe":
        if not _is_authenticated():
            return
        if st.session_state.get("_mj_pending_describe"):
            return
        # 크레딧 확인 (1크레딧)
        from core.credits import check_credits
        from core.credits import get_feature_cost as _gfc2
        ok, msg = check_credits(cfg, _gfc2(cfg, "mj_describe"))
        if not ok:
            st.session_state["_mj_error_msg"] = msg
            st.rerun()
            return
        image_data = result.get("describe_image", "")
        if not image_data:
            return
        st.session_state["_mj_pending_describe"] = {
            "image_data_url": image_data,
        }
        st.rerun()
    elif action == "submit":
        if not _is_authenticated():
            return
        # 이미 대기 중인 요청이 있으면 무시 (중복 방지)
        if st.session_state.get("_mj_pending_submit"):
            return

        # ── 크레딧 확인 (Phase 1) ──
        from core.credits import check_credits
        # Relax 고정: 이미지당 2크레딧 × 4장 = 8
        _cost = 2 * 4
        ok, msg = check_credits(cfg, _cost)
        if not ok:
            st.session_state["_mj_error_msg"] = msg
            st.rerun()
            return

        raw_prompt = result.get("prompt", "")
        if len(raw_prompt) > 10000:
            raw_prompt = raw_prompt[:10000]
        s = result.get("settings", {})

        # 프롬프트에서 --ar, --s 등 MJ 파라미터 추출 → settings 병합
        prompt, s = _parse_prompt_params(raw_prompt, s)

        if prompt:
            tags = _build_tags(s)
            ar = s.get("aspectRatio", "1:1")
            today = datetime.now(timezone.utc).strftime("%b %d, %Y")

            if not sidebar.test_mode:
                # Real API → 로딩 아이템 먼저 표시, 다음 rerun에서 API 호출
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

                # 로그인 사용자 → DB에 저장
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

                st.session_state.mj_gallery.insert(0, new_item)

                # 다음 rerun에서 처리할 대기 요청 저장
                st.session_state["_mj_pending_submit"] = {
                    "prompt": prompt,
                    "settings": s,
                    "attached_images": result.get("attachedImages"),
                    "aspect_ratio": ar,
                    "loading_ts": ts,
                }
            else:
                # Mock ON → 기존 동작 유지 (JS가 10초 후 mock 이미지 전달)
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

                st.session_state.mj_gallery.insert(0, new_item)

            st.rerun()

    # ── 로딩 완료 이벤트 (10초 mock 대기 후 JS에서 전송) ──
    elif action == "loading_complete":
        loading_ts = result.get("loading_ts")
        mock_images = result.get("mock_images", [])
        updated = False
        for item in st.session_state.get("mj_gallery", []):
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
    "tab_id": "mj",
    "title": "Image Create(ex. MJ)",
    "required_features": {"tab.mj"},
    "render": render_mj_tab,
}
