# core/config.py
import os
import streamlit as st
import uuid
import json
from dataclasses import dataclass
from typing import Dict, List, Optional
from pathlib import Path


@dataclass(frozen=True)
class AppConfig:
    # Keys
    legnext_api_key: str
    kling_access_key: str
    kling_secret_key: str

    openai_api_key: str
    openai_model: str

    # DB / limits
    runs_db_path: str
    user_max_concurrency: int
    global_max_concurrency: int

    # TTL
    active_job_ttl_sec: int

    # Tab
    enabled_tabs_default: List[str]
    enabled_tabs_by_school: Dict[str, List[str]]

    tenant_config_dir: str

    def get_enabled_tabs(self, school_id: str) -> List[str]:
        if school_id and school_id in self.enabled_tabs_by_school:
            return self.enabled_tabs_by_school[school_id]
        return self.enabled_tabs_default
    
    def get_enabled_features(self, school_id: str) -> List[str]:
        # 1) tenant json 우선
        t = _load_tenant_json(self.tenant_config_dir, school_id)
        if t and isinstance(t.get("enabled_features"), list):
            return _normalize_str_list(t.get("enabled_features"))

        # 2) 없으면 기존 enabled_tabs 를 feature로 변환해서 fallback
        #    (예: ["legnext","kling"] -> ["tab.legnext","tab.kling"])
        tab_ids = self.get_enabled_tabs(school_id)
        return [f"tab.{x}" for x in tab_ids if str(x).strip()]

    def get_layout(self, school_id: str) -> str:
        t = _load_tenant_json(self.tenant_config_dir, school_id)
        if t and isinstance(t.get("layout"), str):
            return t["layout"]
        return "default"


def _get_secret_or_env(key: str, default: str = "") -> str:
    return (st.secrets.get(key, "") or os.getenv(key, default) or "").strip()

def _parse_csv_list(v: str) -> List[str]:
    return [x.strip() for x in (v or "").split(",") if x.strip()]

def _parse_tabs_by_school(v: str) -> Dict[str, List[str]]:
    if not v:
        return {}
    try:
        raw = json.loads(v)
        if not isinstance(raw, dict):
            return {}
        out: Dict[str, List[str]] = {}
        for k, arr in raw.items():
            if isinstance(k, str) and isinstance(arr, list):
                out[k] = [str(x).strip() for x in arr if str(x).strip()]
        return out
    except Exception:
        return {}

def _normalize_str_list(v) -> List[str]:
    if not isinstance(v, list):
        return []
    return [str(x).strip() for x in v if str(x).strip()]

def _load_json_file(path: Path) -> Optional[dict]:
    try:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None

def _load_tenant_json(tenant_dir: str, school_id: str) -> Optional[dict]:
    """
    school_id.json → 없으면 default.json
    탐색 순서:
      1) {tenant_dir}/{school_id}.json
      2) {tenant_dir}/default.json
      3) ./tenants/{school_id}.json, ./tenants/default.json (안전망)
      4) ./{school_id}.json, ./default.json (안전망)
    """
    sid = (school_id or "default").strip() or "default"

    candidates = []

    base = Path(tenant_dir) if tenant_dir else Path(".")
    candidates += [base / f"{sid}.json", base / "default.json"]

    # 안전망들
    candidates += [Path("tenants") / f"{sid}.json", Path("tenants") / "default.json"]
    candidates += [Path(f"{sid}.json"), Path("default.json")]

    for p in candidates:
        j = _load_json_file(p)
        if isinstance(j, dict):
            return j
    return None

def load_config() -> AppConfig:
    enabled_tabs_default = _parse_csv_list(_get_secret_or_env("ENABLED_TABS", "legnext,kling"))
    enabled_tabs_by_school = _parse_tabs_by_school(_get_secret_or_env("TABS_BY_SCHOOL_JSON", ""))

    # ✅ tenant json 폴더 (없으면 현재 폴더에서 찾도록 "." 기본)
    tenant_config_dir = _get_secret_or_env("TENANT_CONFIG_DIR", ".")

    if "KEY_POOL_JSON" in st.secrets and not os.getenv("KEY_POOL_JSON"):
        os.environ["KEY_POOL_JSON"] = str(st.secrets["KEY_POOL_JSON"])

    return AppConfig(
        legnext_api_key=_get_secret_or_env("MJ_API_KEY", ""),
        kling_access_key=_get_secret_or_env("KLING_ACCESS_KEY", ""),
        kling_secret_key=_get_secret_or_env("KLING_SECRET_KEY", ""),

        openai_api_key=_get_secret_or_env("OPENAI_API_KEY", ""),
        openai_model=_get_secret_or_env("OPENAI_MODEL", "gpt-4o-mini"),

        runs_db_path=_get_secret_or_env("RUNS_DB_PATH", "runs.db"),
        user_max_concurrency=int(_get_secret_or_env("USER_MAX_CONCURRENCY", "1") or "1"),
        global_max_concurrency=int(_get_secret_or_env("GLOBAL_MAX_CONCURRENCY", "2") or "2"),

        active_job_ttl_sec=int(_get_secret_or_env("ACTIVE_JOB_TTL_SEC", str(20 * 60)) or str(20 * 60)),

        enabled_tabs_default=enabled_tabs_default,
        enabled_tabs_by_school=enabled_tabs_by_school,

        tenant_config_dir=tenant_config_dir,
    )


def ensure_session_ids():
    if "session_id" not in st.session_state:
        st.session_state.session_id = str(uuid.uuid4())
    if "user_id" not in st.session_state:
        st.session_state.user_id = "guest"
    if "school_id" not in st.session_state:
        st.session_state.school_id = "default"
