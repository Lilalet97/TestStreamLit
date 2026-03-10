# core/credits.py
"""통합 크레딧 확인/차감 헬퍼.

모든 탭에서 Phase 1 (check_credits) / Phase 2 (deduct_after_success) 호출.
admin/teacher 면제. cost=0이면 무제한 (차감 없음).
"""
import streamlit as st
from core.config import AppConfig
from core.db import get_admin_setting, get_user_balance, deduct_user_balance

_EXEMPT_ROLES = ("admin", "teacher")

DEFAULT_FEATURE_COSTS = {
    "gpt": 1,
    "mj": 5,
    "nanobanana": 5,
    "nanobanana_pro": 10,
    "nanobanana_2": 5,
    "elevenlabs": 5,
    "kling": 7,
    "veo": 7,
    "grok": 7,
}

FEATURE_IDS = ("gpt", "mj", "nanobanana", "nanobanana_2", "nanobanana_pro", "elevenlabs", "kling", "veo", "grok")

FEATURE_LABELS = {
    "gpt": "💬 GPT",
    "mj": "🎨 MJ",
    "nanobanana": "🍌 NB",
    "nanobanana_pro": "🍌 NB Pro",
    "nanobanana_2": "🍌 NB 2",
    "elevenlabs": "🔊 EL",
    "kling": "🎬 Kling",
    "veo": "🎬 Veo",
    "grok": "🎬 Grok",
}

FEATURE_UNITS = {
    "gpt": "회",
    "mj": "장",
    "nanobanana": "장",
    "nanobanana_pro": "장",
    "nanobanana_2": "장",
    "elevenlabs": "개",
    "kling": "초",
    "veo": "초",
    "grok": "초",
}


def get_feature_cost(cfg: AppConfig, feature_id: str) -> int:
    """기능별 단위 비용. admin_settings 우선, 없으면 기본값. 0 = 무제한."""
    val = get_admin_setting(cfg, f"credit_cost.{feature_id}", "")
    if val and val.isdigit():
        return max(0, int(val))
    return DEFAULT_FEATURE_COSTS.get(feature_id, 0)


def check_credits(cfg: AppConfig, cost: int) -> tuple:
    """Phase 1: 통합 잔액 확인. (ok, error_msg) 반환."""
    role = st.session_state.get("auth_role", "student")
    if role in _EXEMPT_ROLES:
        return True, ""

    if cost <= 0:
        return True, ""

    user_id = st.session_state.get("auth_user_id", "")
    if not user_id:
        return True, ""

    balance = get_user_balance(cfg, user_id)
    if balance >= cost:
        return True, ""

    return False, f"크레딧이 부족합니다. (잔여: {balance}, 필요: {cost})"


def deduct_after_success(cfg: AppConfig, cost: int, tab_id: str = "") -> int:
    """Phase 2: 성공 후 통합 잔액에서 차감. 새 잔액 반환. 면제/무료면 -1."""
    role = st.session_state.get("auth_role", "student")
    if role in _EXEMPT_ROLES:
        return -1

    if cost <= 0:
        return -1

    user_id = st.session_state.get("auth_user_id", "")
    if not user_id:
        return -1

    school_id = st.session_state.get("school_id", "")
    ok = deduct_user_balance(cfg, user_id, cost, tab_id=tab_id, school_id=school_id)
    if not ok:
        raise RuntimeError(f"크레딧 차감 실패 (잔여 부족)")

    return get_user_balance(cfg, user_id)
