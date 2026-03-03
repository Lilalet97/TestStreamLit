# providers/gcs_storage.py
"""GCS 미디어 업로드 유틸리티 — Vertex AI SA 인증 재사용.

data URL → GCS 업로드 → 퍼블릭 URL 반환.
GCS_BUCKET_NAME 미설정 시 모든 함수가 no-op (base64 그대로 반환).
"""
import base64
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import quote as url_quote

import requests

from providers.vertex_auth import get_auth_headers

logger = logging.getLogger(__name__)

# GCS JSON API endpoints
_GCS_UPLOAD_URL = "https://storage.googleapis.com/upload/storage/v1/b/{bucket}/o"
_GCS_PUBLIC_URL = "https://storage.googleapis.com/{bucket}/{object_name}"

# 5 MB — simple upload 상한, 초과 시 resumable upload
_SIMPLE_UPLOAD_LIMIT = 5 * 1024 * 1024

# MIME → 파일 확장자
_MIME_EXT = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "image/gif": "gif",
    "video/mp4": "mp4",
    "video/webm": "webm",
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/wav": "wav",
}

# MIME 카테고리 → GCS 하위 경로
_MIME_PREFIX = {
    "image": "images",
    "video": "videos",
    "audio": "audio",
}


# ──────────────────────────────────────────────
#  내부 헬퍼
# ──────────────────────────────────────────────

def _parse_data_url(data_url: str) -> Optional[tuple[str, bytes]]:
    """data URL → (mime_type, raw_bytes). 실패 시 None."""
    m = re.match(r"data:([^;]+);base64,(.+)", data_url, re.DOTALL)
    if not m:
        return None
    mime = m.group(1)
    try:
        raw = base64.b64decode(m.group(2))
    except Exception:
        return None
    return mime, raw


def _make_object_name(mime: str, prefix: str = "") -> str:
    """GCS 오브젝트 경로 생성: {prefix}/{media_type}/{YYYY-MM-DD}/{uuid}.{ext}"""
    media_type = _MIME_PREFIX.get(mime.split("/")[0], "misc")
    ext = _MIME_EXT.get(mime, mime.split("/")[-1])
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    uid = uuid.uuid4().hex
    obj = f"{media_type}/{date_str}/{uid}.{ext}"
    if prefix:
        obj = f"{prefix.strip('/')}/{obj}"
    return obj


def _simple_upload(
    headers: dict, bucket: str, object_name: str,
    raw_bytes: bytes, mime: str, timeout: int,
) -> Optional[str]:
    """5 MB 이하 파일 — simple media upload."""
    url = _GCS_UPLOAD_URL.format(bucket=bucket)
    h = dict(headers)
    h["Content-Type"] = mime

    resp = requests.post(
        url, headers=h,
        params={"uploadType": "media", "name": object_name},
        data=raw_bytes, timeout=timeout,
    )
    if resp.status_code in (200, 201):
        return _GCS_PUBLIC_URL.format(bucket=bucket, object_name=url_quote(object_name, safe="/"))
    logger.warning("GCS simple upload failed (%d): %s", resp.status_code, resp.text[:200])
    return None


def _resumable_upload(
    headers: dict, bucket: str, object_name: str,
    raw_bytes: bytes, mime: str, timeout: int,
) -> Optional[str]:
    """5 MB 초과 파일 — resumable upload (영상 등 대용량)."""
    # 1단계: 업로드 세션 시작
    init_url = _GCS_UPLOAD_URL.format(bucket=bucket)
    h = dict(headers)
    h["Content-Type"] = "application/json"
    h["X-Upload-Content-Type"] = mime
    h["X-Upload-Content-Length"] = str(len(raw_bytes))

    resp = requests.post(
        init_url, headers=h,
        params={"uploadType": "resumable", "name": object_name},
        json={"name": object_name, "contentType": mime},
        timeout=30,
    )
    if resp.status_code != 200:
        logger.warning("GCS resumable init failed (%d): %s", resp.status_code, resp.text[:200])
        return None

    upload_uri = resp.headers.get("Location")
    if not upload_uri:
        logger.warning("GCS resumable init: no Location header")
        return None

    # 2단계: 실제 데이터 업로드
    resp2 = requests.put(
        upload_uri,
        headers={"Content-Type": mime, "Content-Length": str(len(raw_bytes))},
        data=raw_bytes, timeout=timeout,
    )
    if resp2.status_code in (200, 201):
        return _GCS_PUBLIC_URL.format(bucket=bucket, object_name=url_quote(object_name, safe="/"))
    logger.warning("GCS resumable upload failed (%d): %s", resp2.status_code, resp2.text[:200])
    return None


# ──────────────────────────────────────────────
#  Public API
# ──────────────────────────────────────────────

def upload_single(
    sa_json: str,
    bucket: str,
    data_url: str,
    prefix: str = "",
    timeout: int = 120,
) -> Optional[str]:
    """단일 data URL → GCS 업로드. 퍼블릭 URL 반환, 실패 시 None."""
    parsed = _parse_data_url(data_url)
    if not parsed:
        return None

    mime, raw_bytes = parsed
    object_name = _make_object_name(mime, prefix)
    headers = get_auth_headers(sa_json)

    if len(raw_bytes) <= _SIMPLE_UPLOAD_LIMIT:
        return _simple_upload(headers, bucket, object_name, raw_bytes, mime, timeout)
    return _resumable_upload(headers, bucket, object_name, raw_bytes, mime, timeout)


def upload_media_urls(
    sa_json: str,
    bucket: str,
    data_urls: list[str],
    prefix: str = "",
) -> list[str]:
    """data URL 리스트 → GCS 업로드 → URL 리스트 반환.

    개별 실패 시 해당 항목만 원본 data URL 유지 (graceful fallback).
    sa_json 또는 bucket이 비어있으면 입력 그대로 반환.
    """
    if not sa_json or not bucket:
        return data_urls

    result = []
    for du in data_urls:
        if not du or not du.startswith("data:"):
            result.append(du)  # 이미 URL이거나 빈 값
            continue
        gcs_url = upload_single(sa_json, bucket, du, prefix)
        result.append(gcs_url if gcs_url else du)
    return result


def upload_single_media_url(
    sa_json: str,
    bucket: str,
    data_url: str,
    prefix: str = "",
) -> str:
    """단일 data URL 업로드. GCS URL 또는 원본 반환."""
    if not sa_json or not bucket or not data_url or not data_url.startswith("data:"):
        return data_url
    gcs_url = upload_single(sa_json, bucket, data_url, prefix)
    return gcs_url if gcs_url else data_url


def resolve_to_data_url(url: str, timeout: int = 30) -> str:
    """URL → data URL 변환 (Gemini inline_data용).

    - data: URL → 그대로 반환
    - https: URL → 다운로드 후 base64 인코딩
    - 실패 시 원본 URL 반환
    """
    if not url:
        return url
    if url.startswith("data:"):
        return url

    try:
        resp = requests.get(url, timeout=timeout)
        if resp.status_code != 200:
            logger.warning("resolve_to_data_url download failed (%d): %s", resp.status_code, url[:100])
            return url

        mime = resp.headers.get("Content-Type", "").split(";")[0].strip()
        if not mime:
            # URL 확장자로 추측
            _ext_map = {
                ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".webp": "image/webp", ".gif": "image/gif",
                ".mp4": "video/mp4", ".mp3": "audio/mpeg",
            }
            for ext, m in _ext_map.items():
                if url.lower().endswith(ext):
                    mime = m
                    break
            else:
                mime = "application/octet-stream"

        b64 = base64.b64encode(resp.content).decode("ascii")
        return f"data:{mime};base64,{b64}"
    except Exception as e:
        logger.warning("resolve_to_data_url exception: %s — %s", url[:100], e)
        return url
