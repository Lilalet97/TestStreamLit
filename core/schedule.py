# core/schedule.py
"""수업 시간표 기반 접근 제어 로직.

규칙:
- 어떤 학교의 수업 시간이 현재 진행 중이면, 해당 학교 학생만 전체 탭 접근 가능.
- 다른 학교 학생은 갤러리 탭만 사용 가능.
- 수업이 없는 시간에는 모든 학교가 전체 탭 사용 가능.
- admin / viewer / teacher 역할은 항상 전체 접근 가능.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional

from core.config import AppConfig
from core.db import get_active_class_now


@dataclass(frozen=True)
class AccessResult:
    has_full_access: bool
    active_school_id: Optional[str] = None   # 현재 수업 중인 학교
    active_label: Optional[str] = None       # 수업 이름
    active_end_hour: int = 0
    active_end_minute: int = 0


def check_access(cfg: AppConfig, user_school_id: str, user_role: str) -> AccessResult:
    """사용자의 현재 접근 권한을 확인.

    Returns:
        AccessResult — has_full_access가 False이면 갤러리만 허용.
    """
    # admin / viewer / teacher는 항상 전체 접근
    if user_role in ("admin", "viewer", "teacher"):
        return AccessResult(has_full_access=True)

    active = get_active_class_now(cfg)
    if active is None:
        # 현재 수업 없음 → 모두 접근 가능
        return AccessResult(has_full_access=True)

    # 수업 중인 학교와 같은 학교 → 전체 접근
    if active["school_id"] == user_school_id:
        return AccessResult(has_full_access=True)

    # 다른 학교 학생 → 갤러리만
    return AccessResult(
        has_full_access=False,
        active_school_id=active["school_id"],
        active_label=active.get("label", ""),
        active_end_hour=active["end_hour"],
        active_end_minute=active["end_minute"],
    )
