# providers/google_imagen.py
"""Google Imagen / Gemini 이미지 생성·편집 API 호출.

Google AI Studio에서 발급한 API 키를 사용.
- 생성: Imagen 4 (models/imagen-4.0-generate-001:predict)
- 편집: Gemini (models/gemini-2.5-flash-image:generateContent)
인증: x-goog-api-key 헤더
"""
import base64
import re

import requests

GOOGLE_AI_BASE = "https://generativelanguage.googleapis.com/v1beta"

# Imagen 4 모델 (Imagen 3은 종료됨)
DEFAULT_MODEL = "imagen-4.0-generate-001"
# Gemini 이미지 편집 모델
EDIT_MODEL = "gemini-2.5-flash-image"


def generate_images(
    api_key: str,
    prompt: str,
    model: str = DEFAULT_MODEL,
    aspect_ratio: str = "1:1",
    num_images: int = 1,
    negative_prompt: str = "",
) -> list[str]:
    """Imagen API 호출 → image data URL 리스트 반환."""
    url = f"{GOOGLE_AI_BASE}/models/{model}:predict"

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

    resp = requests.post(
        url,
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        },
        json=payload,
        timeout=120,
    )
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


def edit_image(
    api_key: str,
    prompt: str,
    source_image_data_url: str,
    aspect_ratio: str = "1:1",
    model: str = EDIT_MODEL,
) -> list[str]:
    """Gemini generateContent API로 이미지 편집 → image data URL 리스트 반환."""
    url = f"{GOOGLE_AI_BASE}/models/{model}:generateContent"

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

    resp = requests.post(
        url,
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        },
        json=payload,
        timeout=120,
    )
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
