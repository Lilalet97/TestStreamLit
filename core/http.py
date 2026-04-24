# core/http.py
import requests

_MAX_RESPONSE_BYTES = 50 * 1024 * 1024  # 50MB


def _safe_json(resp: requests.Response):
    try:
        return resp.json()
    except Exception:
        return None


def _read_with_limit(resp: requests.Response):
    """Read response with size limit, returning (text, json)."""
    content_len = resp.headers.get("Content-Length")
    if content_len and int(content_len) > _MAX_RESPONSE_BYTES:
        resp.close()
        raise RuntimeError(f"Response too large: {content_len} bytes (limit {_MAX_RESPONSE_BYTES})")

    chunks = []
    downloaded = 0
    for chunk in resp.iter_content(chunk_size=1024 * 1024):
        downloaded += len(chunk)
        if downloaded > _MAX_RESPONSE_BYTES:
            resp.close()
            raise RuntimeError(f"Response exceeded {_MAX_RESPONSE_BYTES} bytes")
        chunks.append(chunk)
    text = b"".join(chunks).decode("utf-8", errors="replace")

    json_body = None
    try:
        import json as _json
        json_body = _json.loads(text)
    except Exception:
        pass

    return text, json_body


def http_post_json(url: str, headers: dict, payload: dict, timeout: int = 30):
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=timeout, stream=True)
        text, json_body = _read_with_limit(r)
        return r.status_code, text, json_body
    except Exception as e:
        return -1, str(e), None


def http_get_json(url: str, headers: dict, timeout: int = 30):
    try:
        r = requests.get(url, headers=headers, timeout=timeout, stream=True)
        text, json_body = _read_with_limit(r)
        return r.status_code, text, json_body
    except Exception as e:
        return -1, str(e), None
