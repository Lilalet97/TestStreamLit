# ui/floating_materials.py
"""플로팅 강의자료 컴포넌트 — Google Drive 공개 폴더 임베드."""
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from core.config import AppConfig
from core.auth import current_user
from core.db import get_admin_setting

_COMPONENT_DIR = Path(__file__).resolve().parent / "components" / "floating_materials"
_materials_component_func = components.declare_component("floating_materials", path=str(_COMPONENT_DIR))


def _materials_component(folder_id: str, key: str = "floating_materials"):
    return _materials_component_func(
        folder_id=folder_id,
        key=key,
        default=None,
    )


@st.fragment()
def _materials_fragment(cfg: AppConfig, school_id: str):
    """강의자료 프래그먼트 — Drive 폴더 ID를 컴포넌트에 전달."""
    folder_id = get_admin_setting(cfg, f"drive_folder.{school_id}", "")
    if not folder_id:
        return
    _materials_component(folder_id=folder_id)


def render_floating_materials(cfg: AppConfig):
    """teacher/student 역할용 플로팅 강의자료 렌더링."""
    user = current_user()
    if not user or user.role not in ("teacher", "student"):
        return
    _materials_fragment(cfg, user.school_id)
