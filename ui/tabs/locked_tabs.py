# ui/tabs/locked_tabs.py
# ──────────────────────────────────────────────
# 잠금(준비중) 탭 모음
# - 새 탭 추가: LOCKED_TABS 리스트에 dict 추가
# - 전체 비활성화: registry.py에서 이 파일 import를 주석 처리
# - 개별 제거: 해당 dict를 삭제하고 tenant JSON에서 feature 제거
# ──────────────────────────────────────────────

from core.config import AppConfig
from ui.sidebar import SidebarState


def _render_locked(cfg: AppConfig, sidebar: SidebarState):
    """잠긴 탭은 선택 자체가 불가하므로 render가 호출될 일 없음."""
    pass


LOCKED_TABS = [
    {
        "tab_id": "ai_presentation",
        "title": "AI Presentation",
        "required_features": {"tab.ai_presentation"},
        "render": _render_locked,
        "locked": True,
    },
    {
        "tab_id": "ai_translation",
        "title": "AI Translation",
        "required_features": {"tab.ai_translation"},
        "render": _render_locked,
        "locked": True,
    },
    {
        "tab_id": "voice_clone",
        "title": "Voice Clone",
        "required_features": {"tab.voice_clone"},
        "render": _render_locked,
        "locked": True,
    },
    {
        "tab_id": "ai_avatar",
        "title": "AI Avatar",
        "required_features": {"tab.ai_avatar"},
        "render": _render_locked,
        "locked": True,
    },
    {
        "tab_id": "video_edit",
        "title": "Video Edit",
        "required_features": {"tab.video_edit"},
        "render": _render_locked,
        "locked": True,
    },
    {
        "tab_id": "deepfake",
        "title": "Deepfake",
        "required_features": {"tab.deepfake"},
        "render": _render_locked,
        "locked": True,
    },
]
