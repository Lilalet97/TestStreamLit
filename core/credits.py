# core/credits.py
"""통합 크레딧 확인/차감 헬퍼.

모든 탭에서 Phase 1 (check_credits) / Phase 2 (deduct_after_success) 호출.
admin/teacher 면제. cost=0이면 무제한 (차감 없음).
"""
import streamlit as st
from core.config import AppConfig
from core.db import (
    get_admin_setting, get_user_balance, deduct_user_balance,
    reserve_user_balance, rollback_user_balance, confirm_reserved_balance,
)

_EXEMPT_ROLES = ("admin", "teacher")

DEFAULT_FEATURE_COSTS = {
    "gpt": 1,
    "mj": 8,
    "nanobanana": 5,
    "nanobanana_pro": 10,
    "nanobanana_2": 5,
    "el_tts": 5,
    "el_vtv": 10,
    "el_sfx": 2,
    "el_clone": 0,
    "kling": 7,
    "veo": 7,
    "grok": 7,
    "ltx": 7,
    "mj_describe": 1,
}

FEATURE_IDS = ("gpt", "mj", "nanobanana", "nanobanana_2", "nanobanana_pro",
               "el_tts", "el_vtv", "el_sfx", "el_clone", "kling", "veo", "grok", "ltx", "mj_describe")

FEATURE_LABELS = {
    "gpt": "💬 GPT",
    "mj": "🎨 MJ",
    "nanobanana": "🍌 NB",
    "nanobanana_pro": "🍌 NB Pro",
    "nanobanana_2": "🍌 NB 2",
    "el_tts": "🔊 TTS",
    "el_vtv": "🔊 VTV",
    "el_sfx": "🔊 SFX",
    "el_clone": "🔊 Clone",
    "kling": "🎬 Kling",
    "veo": "🎬 Veo",
    "grok": "🎬 Grok",
    "ltx": "🎬 LTX",
    "mj_describe": "🔍 Describe",
}

FEATURE_UNITS = {
    "gpt": "회",
    "mj": "장",
    "nanobanana": "장",
    "nanobanana_pro": "장",
    "nanobanana_2": "장",
    "el_tts": "개",
    "el_vtv": "개",
    "el_sfx": "개",
    "el_clone": "회",
    "kling": "초",
    "veo": "초",
    "grok": "초",
    "ltx": "초",
    "mj_describe": "회",
}


DEFAULT_API_UNIT_COSTS: dict[str, float] = {
    "gpt": 0.001,
    "mj": 0.003,
    "nanobanana": 0.004,
    "nanobanana_pro": 0.006,
    "nanobanana_2": 0.003,
    "el_tts": 0.01,
    "el_vtv": 0.02,
    "el_sfx": 0.005,
    "el_clone": 0.0,
    "kling": 0.05,
    "veo": 0.10,
    "grok": 0.05,
    "ltx": 0.06,
    "mj_describe": 0.0,
}

DEFAULT_EXCHANGE_RATE = 1400.0


def get_api_unit_cost(cfg: AppConfig, feature_id: str) -> float:
    """기능별 API 단가 (USD). admin_settings 우선."""
    val = get_admin_setting(cfg, f"api_unit_cost.{feature_id}", "")
    if val:
        try:
            return max(0.0, float(val))
        except ValueError:
            pass
    return DEFAULT_API_UNIT_COSTS.get(feature_id, 0.0)


def get_exchange_rate(cfg: AppConfig) -> float:
    """USD→KRW 환율. admin_settings 우선."""
    val = get_admin_setting(cfg, "api_exchange_rate", "")
    if val:
        try:
            return max(1.0, float(val))
        except ValueError:
            pass
    return DEFAULT_EXCHANGE_RATE


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
        return False, "로그인이 필요합니다."

    balance = get_user_balance(cfg, user_id)
    if balance >= cost:
        return True, ""

    return False, f"크레딧이 부족합니다. (잔여: {balance}, 필요: {cost})"


def reserve_credits(cfg: AppConfig, cost: int) -> tuple:
    """선차감: 잔액에서 즉시 차감(예약). API 실패 시 rollback_credits 호출 필요.
    Returns (ok, error_msg)."""
    role = st.session_state.get("auth_role", "student")
    if role in _EXEMPT_ROLES:
        return True, ""

    if cost <= 0:
        return True, ""

    user_id = st.session_state.get("auth_user_id", "")
    if not user_id:
        return False, "로그인이 필요합니다."

    ok = reserve_user_balance(cfg, user_id, cost)
    if not ok:
        balance = get_user_balance(cfg, user_id)
        return False, f"크레딧이 부족합니다. (잔여: {balance}, 필요: {cost})"
    return True, ""


def rollback_credits(cfg: AppConfig, cost: int):
    """API 실패 시 선차감 복원."""
    role = st.session_state.get("auth_role", "student")
    if role in _EXEMPT_ROLES or cost <= 0:
        return

    user_id = st.session_state.get("auth_user_id", "")
    if not user_id:
        return  # 로그인하지 않은 경우 복원할 예약 없음

    rollback_user_balance(cfg, user_id, cost)


def confirm_credits(cfg: AppConfig, cost: int, tab_id: str = ""):
    """선차감 확정 — usage_log 기록."""
    role = st.session_state.get("auth_role", "student")
    if role in _EXEMPT_ROLES or cost <= 0:
        return

    user_id = st.session_state.get("auth_user_id", "")
    if not user_id:
        return  # 로그인하지 않은 경우 확정할 예약 없음

    school_id = st.session_state.get("school_id", "")
    confirm_reserved_balance(cfg, user_id, cost, tab_id=tab_id, school_id=school_id)


def deduct_after_success(cfg: AppConfig, cost: int, tab_id: str = "") -> int:
    """Phase 2: 성공 후 통합 잔액에서 차감. 새 잔액 반환. 면제/무료면 -1.
    (기존 호환 — 선차감 미사용 경로용)"""
    role = st.session_state.get("auth_role", "student")
    if role in _EXEMPT_ROLES:
        return -1

    if cost <= 0:
        return -1

    user_id = st.session_state.get("auth_user_id", "")
    if not user_id:
        raise RuntimeError("로그인이 필요합니다.")

    school_id = st.session_state.get("school_id", "")
    ok = deduct_user_balance(cfg, user_id, cost, tab_id=tab_id, school_id=school_id)
    if not ok:
        raise RuntimeError(f"크레딧 차감 실패 (잔여 부족)")

    return get_user_balance(cfg, user_id)
