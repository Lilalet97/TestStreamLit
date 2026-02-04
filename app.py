# app.py
import streamlit as st
import os, json

from core.config import load_config, ensure_session_ids
from core.db import init_db, cleanup_orphan_active_jobs
from ui.sidebar import render_sidebar
from ui.run_detail import maybe_open_run_detail_dialog
from ui.registry import get_all_tabs, filter_tabs
from core.key_pool import bootstrap as key_pool_bootstrap


def main():
    st.set_page_config(page_title="Generative AI Multi-API Full Tester", layout="wide")

    cfg = load_config()
    if "school_id" not in st.session_state:
        st.session_state.school_id = "default"
    ensure_session_ids()

    init_db(cfg)
    key_pool_bootstrap(cfg)
    raw = os.getenv("KEY_POOL_JSON") or st.secrets.get("KEY_POOL_JSON", "")
    st.sidebar.write("KEY_POOL_JSON loaded:", bool(raw))
    if raw:
        kp = json.loads(raw)
        st.sidebar.write({k: len(v) for k, v in kp.items()})

    if "_did_cleanup_active_jobs" not in st.session_state:
        cleanup_orphan_active_jobs(cfg)
        st.session_state["_did_cleanup_active_jobs"] = True

    sidebar_state = render_sidebar(cfg)

    st.title("🚀 Generative AI Multi-API Full Tester")

    maybe_open_run_detail_dialog(cfg)

    school_id = st.session_state.get("school_id", "default")

    # ✅ enabled_features는 tenant json(default.json/school_a.json)을 우선 사용
    enabled_features = set(cfg.get_enabled_features(school_id))

    all_tabs = get_all_tabs()
    visible_tabs = filter_tabs(all_tabs, enabled_features)

    if not visible_tabs:
        st.warning(
            f"이 학교({school_id})는 현재 오픈된 탭이 없습니다.\n"
            f"- enabled_features: {sorted(enabled_features)}"
        )
        return

    tab_objs = st.tabs([t.title for t in visible_tabs])
    for tab_obj, tab_def in zip(tab_objs, visible_tabs):
        with tab_obj:
            tab_def.render(cfg, sidebar_state)


if __name__ == "__main__":
    main()
