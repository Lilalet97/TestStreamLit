# core/http.py
import requests


def _safe_json(resp: requests.Response):
    try:
        return resp.json()
    except Exception:
        return None


def http_post_json(url: str, headers: dict, payload: dict, timeout: int = 30):
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=timeout)
        return r.status_code, r.text, _safe_json(r)
    except Exception as e:
        return -1, str(e), None


def http_get_json(url: str, headers: dict, timeout: int = 30):
    try:
        r = requests.get(url, headers=headers, timeout=timeout)
        return r.status_code, r.text, _safe_json(r)
    except Exception as e:
        return -1, str(e), None
