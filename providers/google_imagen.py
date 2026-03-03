# providers/google_imagen.py
"""Google Imagen / Gemini 이미지 생성·편집 API 호출 — AI Studio 인증.

Google AI Studio API 키 방식 사용.
- 생성: Imagen 4 (predict)
- 편집/생성: Gemini 2.5 Flash Image (generateContent)
인증: API 키 (URL 쿼리 파라미터)

# [VERTEX AI 복원 시]
# from providers.vertex_auth import get_auth_headers, get_vertex_url
# 각 함수에서 sa_json, project_id, location 파라미터 복원
# url = get_vertex_url(project_id, location, model, method)
# headers = get_auth_headers(sa_json)
"""
import re

import requests

from providers.vertex_auth import get_aistudio_url, get_aistudio_headers

# Imagen 4 모델 (Imagen 3은 종료됨)
DEFAULT_MODEL = "imagen-4.0-generate-001"
# Gemini 이미지 편집 모델
# [VERTEX AI] EDIT_MODEL = "gemini-2.5-flash-preview-image-generation"
EDIT_MODEL = "gemini-2.5-flash-image"


def generate_images(
    api_key: str,
    prompt: str,
    model: str = EDIT_MODEL,
    aspect_ratio: str = "1:1",
    num_images: int = 1,
    negative_prompt: str = "",
    # [VERTEX AI] sa_json: str = "", project_id: str = "", location: str = "",
) -> list[str]:
    """Imagen API 호출 → image data URL 리스트 반환."""
    url = get_aistudio_url(model, "predict", api_key)
    headers = get_aistudio_headers()
    # [VERTEX AI] url = get_vertex_url(project_id, location, model, "predict")
    # [VERTEX AI] headers = get_auth_headers(sa_json)

    params = {
        "sampleCount": min(num_images, 4),
        "aspectRatio": aspect_ratio,
    }
    if negative_prompt:
        params["negativePrompt"] = negative_prompt

    payload = {
        "instances": [{"prompt": prompt}],
        "parameters": params,
    }

    resp = requests.post(url, headers=headers, json=payload, timeout=120)
    if resp.status_code != 200:
        raise RuntimeError(
            f"Google Imagen API {resp.status_code}: {resp.text[:300]}"
        )

    data = resp.json()
    predictions = data.get("predictions", [])
    urls = []
    for pred in predictions:
        b64 = pred.get("bytesBase64Encoded", "")
        mime = pred.get("mimeType", "image/png")
        if b64:
            urls.append(f"data:{mime};base64,{b64}")
    return urls


def _data_url_to_parts(data_url: str) -> dict:
    """data:image/png;base64,... → inline_data dict."""
    m = re.match(r"data:([^;]+);base64,(.+)", data_url, re.DOTALL)
    if not m:
        raise ValueError("Invalid data URL format")
    return {"inline_data": {"mime_type": m.group(1), "data": m.group(2)}}


def gemini_generate(
    api_key: str,
    parts: list,
    aspect_ratio: str = "1:1",
    num_images: int = 1,
    model: str = EDIT_MODEL,
    # [VERTEX AI] sa_json: str = "", project_id: str = "", location: str = "",
) -> list[str]:
    """Gemini generateContent API로 이미지 생성/편집 → image data URL 리스트 반환.

    Args:
        parts: content parts 리스트. 각 원소는:
            - {"text": "..."}: 텍스트 파트
            - str (data URL): inline_data로 변환
        num_images: Gemini 1회 호출당 1장 → num_images만큼 반복 호출.
    """
    url = get_aistudio_url(model, "generateContent", api_key)
    headers = get_aistudio_headers()
    # [VERTEX AI] url = get_vertex_url(project_id, location, model, "generateContent")
    # [VERTEX AI] headers = get_auth_headers(sa_json)

    # parts → API 포맷 변환
    api_parts = []
    for p in parts:
        if isinstance(p, dict) and "text" in p:
            api_parts.append(p)
        elif isinstance(p, str):
            if p.startswith("http"):
                from providers.gcs_storage import resolve_to_data_url
                p = resolve_to_data_url(p)
            api_parts.append(_data_url_to_parts(p))
        else:
            api_parts.append(p)

    payload = {
        "contents": [{"parts": api_parts}],
        "generationConfig": {
            "responseModalities": ["TEXT", "IMAGE"],
            "imageConfig": {
                "aspectRatio": aspect_ratio,
            },
        },
    }

    all_urls: list[str] = []
    for _ in range(num_images):
        resp = requests.post(url, headers=headers, json=payload, timeout=120)
        if resp.status_code != 200:
            raise RuntimeError(
                f"Gemini API {resp.status_code}: {resp.text[:300]}"
            )

        data = resp.json()
        candidates = data.get("candidates", [])
        for candidate in candidates:
            # 안전 필터 체크
            if candidate.get("finishReason") == "SAFETY":
                continue
            c_parts = candidate.get("content", {}).get("parts", [])
            for part in c_parts:
                inline = part.get("inlineData") or part.get("inline_data")
                if inline:
                    b64 = inline.get("data", "")
                    mime = inline.get("mimeType") or inline.get("mime_type") or "image/png"
                    if b64:
                        all_urls.append(f"data:{mime};base64,{b64}")

    return all_urls


def edit_image(
    api_key: str,
    prompt: str,
    source_image_data_url: str,
    aspect_ratio: str = "1:1",
    model: str = EDIT_MODEL,
    # [VERTEX AI] sa_json: str = "", project_id: str = "", location: str = "",
) -> list[str]:
    """Gemini generateContent API로 이미지 편집 → image data URL 리스트 반환."""
    url = get_aistudio_url(model, "generateContent", api_key)
    headers = get_aistudio_headers()
    # [VERTEX AI] url = get_vertex_url(project_id, location, model, "generateContent")
    # [VERTEX AI] headers = get_auth_headers(sa_json)

    payload = {
        "contents": [{
            "parts": [
                {"text": prompt},
                _data_url_to_parts(source_image_data_url),
            ]
        }],
        "generationConfig": {
            "responseModalities": ["TEXT", "IMAGE"],
            "imageConfig": {
                "aspectRatio": aspect_ratio,
            },
        },
    }

    resp = requests.post(url, headers=headers, json=payload, timeout=120)
    if resp.status_code != 200:
        raise RuntimeError(
            f"Gemini Image Edit API {resp.status_code}: {resp.text[:300]}"
        )

    data = resp.json()
    candidates = data.get("candidates", [])
    urls = []
    for candidate in candidates:
        parts = candidate.get("content", {}).get("parts", [])
        for part in parts:
            inline = part.get("inlineData") or part.get("inline_data")
            if inline:
                b64 = inline.get("data", "")
                mime = inline.get("mimeType") or inline.get("mime_type") or "image/png"
                if b64:
                    urls.append(f"data:{mime};base64,{b64}")
    return urls
