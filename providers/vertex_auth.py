# providers/vertex_auth.py
"""Vertex AI 공유 인증 모듈.

Service Account JSON → OAuth2 Bearer 토큰 발급/캐시/갱신.
google_imagen, google_veo 등 Vertex AI 기반 provider가 공유.
"""
import hashlib
import json
import threading

from google.oauth2 import service_account
import google.auth.transport.requests

_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]
_CRED_CACHE: dict = {}
_LOCK = threading.Lock()


def get_auth_headers(sa_json: str) -> dict:
    """SA JSON 문자열 → Bearer 토큰 포함 헤더 반환.

    - SA JSON 해시 기반으로 Credentials 객체 캐시
    - 토큰 만료 시 자동 갱신
    """
    cache_key = hashlib.md5(sa_json.encode()).hexdigest()

    with _LOCK:
        cred = _CRED_CACHE.get(cache_key)
        if cred is None:
            info = json.loads(sa_json)
            cred = service_account.Credentials.from_service_account_info(
                info, scopes=_SCOPES,
            )
            _CRED_CACHE[cache_key] = cred

        if not cred.valid:
            cred.refresh(google.auth.transport.requests.Request())

    return {
        "Authorization": f"Bearer {cred.token}",
        "Content-Type": "application/json",
    }


def get_vertex_url(
    project_id: str, location: str, model: str, method: str,
) -> str:
    """Vertex AI 엔드포인트 URL 생성.

    Returns:
        https://{location}-aiplatform.googleapis.com/v1/
        projects/{project_id}/locations/{location}/
        publishers/google/models/{model}:{method}
    """
    base = f"https://{location}-aiplatform.googleapis.com/v1"
    return (
        f"{base}/projects/{project_id}/locations/{location}"
        f"/publishers/google/models/{model}:{method}"
    )


# ── Google AI Studio ──

def get_aistudio_url(model: str, method: str, api_key: str) -> str:
    """Google AI Studio 엔드포인트 URL (API 키 포함)."""
    base = "https://generativelanguage.googleapis.com/v1beta"
    return f"{base}/models/{model}:{method}?key={api_key}"


def get_aistudio_headers() -> dict:
    """AI Studio용 헤더 (인증 불필요, Content-Type만)."""
    return {"Content-Type": "application/json"}
