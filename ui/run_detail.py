# ui/run_detail.py
import json
import streamlit as st

from core.config import AppConfig
from core.db import get_run
from core.redact import json_dumps_safe


def _render_body(cfg: AppConfig, run_id: str):
    r = get_run(cfg, run_id)
    if not r:
        st.warning("선택한 run_id를 찾지 못했습니다.")
        return

    st.write(f"run_id: {r['run_id']}")
    st.write(f"created_at: {r['created_at']}")
    st.write(f"provider/operation: {r['provider']} / {r['operation']}")
    st.write(f"state: {r['state']}  | http: {r['http_status']}")
    if r["job_id"]:
        st.write(f"job_id/task_id: {r['job_id']}")
    if r["duration_ms"] is not None:
        st.write(f"duration_ms: {r['duration_ms']}")

    st.markdown("### 요청(레시피)")
    if r["request_json"]:
        st.code(r["request_json"], language="json")

    st.markdown("### 응답(JSON)")
    if r["response_json"]:
        st.code(r["response_json"], language="json")

    if r["output_json"]:
        st.markdown("### 최종 출력/결과")
        st.code(r["output_json"], language="json")

    if r["error_text"]:
        st.markdown("### 에러 로그")
        st.code(r["error_text"])

    if r["gpt_analysis"]:
        st.markdown("### 원인 분석")
        st.code(r["gpt_analysis"], language="json")

    bundle = {
        "run_id": r["run_id"],
        "created_at": r["created_at"],
        "user_id": r["user_id"],
        "session_id": r["session_id"],
        "provider": r["provider"],
        "operation": r["operation"],
        "endpoint": r["endpoint"],
        "state": r["state"],
        "job_id": r["job_id"],
        "http_status": r["http_status"],
        "request": json.loads(r["request_json"]) if r["request_json"] else None,
        "response_json": json.loads(r["response_json"]) if r["response_json"] else None,
        "output": json.loads(r["output_json"]) if r["output_json"] else None,
        "gpt_analysis": json.loads(r["gpt_analysis"]) if r["gpt_analysis"] else None,
        "error_text": r["error_text"],
    }
    st.download_button(
        "⬇️ 레시피/로그 다운로드(JSON)",
        data=json_dumps_safe(bundle),
        file_name=f"run_{r['run_id']}.json",
        mime="application/json",
    )


def maybe_open_run_detail_dialog(cfg: AppConfig):
    run_id = st.session_state.get("selected_run_id")
    if not run_id:
        return

    # sidebar에서 "선택 변화" 감지했을 때만 자동 오픈
    if not st.session_state.get("_open_run_detail"):
        return

    st.session_state["_open_run_detail"] = False

    # Streamlit 버전에 따라 dialog 유무가 갈릴 수 있어서 fallback 제공
    if hasattr(st, "dialog"):
        @st.dialog("📌 실행 상세(레시피/로그)")
        def _dlg():
            _render_body(cfg, run_id)
        _dlg()
    else:
        # fallback: expander
        with st.expander("📌 실행 상세(레시피/로그)", expanded=True):
            _render_body(cfg, run_id)
