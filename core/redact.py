# core/redact.py
import json
import re

_SENSITIVE_KEY_RE = re.compile(r"(api[-_ ]?key|secret|token|authorization|bearer|password|x-api-key)", re.IGNORECASE)


def _redact_value(v):
    if v is None:
        return None
    if isinstance(v, str):
        if len(v) <= 6:
            return "***"
        return v[:2] + "***" + v[-2:]
    return "***"


def redact_obj(obj):
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if _SENSITIVE_KEY_RE.search(str(k)):
                out[k] = _redact_value(v)
            else:
                out[k] = redact_obj(v)
        return out
    if isinstance(obj, list):
        return [redact_obj(x) for x in obj]
    return obj


def json_dumps_safe(obj):
    try:
        return json.dumps(obj, ensure_ascii=False, indent=2)
    except Exception:
        try:
            return json.dumps(str(obj), ensure_ascii=False, indent=2)
        except Exception:
            return str(obj)
