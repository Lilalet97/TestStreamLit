# core/config.py
import logging
import os
import re
import streamlit as st
import uuid
import json
from dataclasses import dataclass
from typing import Dict, List, Optional
from pathlib import Path

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AppConfig:
    # Keys
    openai_api_key: str

    # Models (프로바이더별 사용 모델)
    openai_model: str
    kling_model: str
    elevenlabs_model: str
    elevenlabs_vtv_model: str
    google_imagen_model: str
    google_imagen_model_pro: str
    google_imagen_model_2: str
    google_veo_model: str
    grok_model: str

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

    # Suno
    suno_accounts_json: str = "[]"

    # Vertex AI (google_imagen, google_veo 공용)
    vertex_sa_json: str = ""
    vertex_location: str = "us-central1"

    # GCS (미디어 업로드 — 미설정 시 기존 base64 방식 유지)
    gcs_bucket_name: str = ""

    debug_auth: bool = False

    def get_suno_accounts(self) -> List[dict]:
        """secrets에서 로드한 Suno 계정 목록 반환."""
        try:
            return json.loads(self.suno_accounts_json)
        except Exception:
            return []

    def get_suno_account(self, suno_id: int) -> Optional[dict]:
        """특정 번호의 Suno 계정 반환. 없으면 None."""
        for acc in self.get_suno_accounts():
            if acc.get("id") == suno_id:
                return acc
        return None

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

    # ── Branding ──

    def get_branding(self, school_id: str) -> dict:
        """tenant JSON에서 branding 딕셔너리를 반환. 없으면 빈 dict."""
        t = _load_tenant_json(self.tenant_config_dir, school_id)
        if t and isinstance(t.get("branding"), dict):
            return t["branding"]
        return {}

    def get_page_title(self, school_id: str) -> str:
        return self.get_branding(school_id).get("page_title", "AIMZ AI 툴 프로젝트")

    def get_browser_tab_title(self, school_id: str) -> str:
        return self.get_branding(school_id).get(
            "browser_tab_title", "Generative AI Multi-API Full Tester"
        )

    def get_logo_path(self, school_id: str) -> Optional[str]:
        path = self.get_branding(school_id).get("logo_path")
        if path and Path(path).exists():
            return path
        return None


def _get_secret_or_env(key: str, default: str = "") -> str:
    try:
        v = str(st.secrets.get(key, "") or "").strip()
    except Exception:
        v = ""
    if not v:
        v = (os.getenv(key, default) or "").strip()
    return v

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

    # Path traversal 방어: school_id에 허용된 문자만 통과
    if not re.match(r'^[a-zA-Z0-9_-]+$', sid):
        _log.warning("Invalid school_id rejected: %s", sid[:50])
        return {}

    candidates = []

    base = Path(tenant_dir) if tenant_dir else Path(".")
    candidates += [base / f"{sid}.json", base / "default.json"]

    # 안전망들
    candidates += [Path("tenants") / f"{sid}.json", Path("tenants") / "default.json"]
    candidates += [Path(f"{sid}.json"), Path("default.json")]

    base_resolved = base.resolve()
    for p in candidates:
        # resolved-path check: 파일이 base 디렉토리 바깥을 가리키지 않는지 확인
        if not p.resolve().is_relative_to(base_resolved):
            continue
        j = _load_json_file(p)
        if isinstance(j, dict):
            return j
    return None

def _extract_from_pool(provider: str, field: str = "api_key") -> str:
    """KEY_POOL_JSON에서 특정 프로바이더의 첫 번째 엔트리 필드를 추출."""
    raw = os.getenv("KEY_POOL_JSON", "")
    if not raw:
        try:
            raw = st.secrets.get("KEY_POOL_JSON", "")
        except Exception:
            pass
    if not raw:
        return ""
    try:
        pool = json.loads(raw)
        items = pool.get(provider, [])
        if items and isinstance(items, list):
            return (items[0].get(field) or "").strip()
    except Exception:
        pass
    return ""


def load_config() -> AppConfig:
    enabled_tabs_default = _parse_csv_list(_get_secret_or_env("ENABLED_TABS", "gpt,mj"))
    enabled_tabs_by_school = _parse_tabs_by_school(_get_secret_or_env("TABS_BY_SCHOOL_JSON", ""))

    # ✅ tenant json 폴더 (없으면 현재 폴더에서 찾도록 "." 기본)
    tenant_config_dir = _get_secret_or_env("TENANT_CONFIG_DIR", ".")
    debug_auth = os.getenv("DEBUG_AUTH", "0") == "1"

    try:
        if "KEY_POOL_JSON" in st.secrets and not os.getenv("KEY_POOL_JSON"):
            os.environ["KEY_POOL_JSON"] = str(st.secrets["KEY_POOL_JSON"])
    except Exception:
        pass

    # OPENAI_API_KEY: 개별 시크릿이 없으면 KEY_POOL_JSON의 첫 번째 openai 키에서 자동 추출
    openai_api_key = _get_secret_or_env("OPENAI_API_KEY", "")
    if not openai_api_key:
        openai_api_key = _extract_from_pool("openai", "api_key")

    return AppConfig(
        openai_api_key=openai_api_key,

        openai_model=_get_secret_or_env("OPENAI_MODEL", "gpt-4o-mini"),
        kling_model=_get_secret_or_env("KLING_MODEL", "kling-v2.6-std"),
        elevenlabs_model=_get_secret_or_env("ELEVENLABS_MODEL", "eleven_multilingual_v2"),
        elevenlabs_vtv_model=_get_secret_or_env("ELEVENLABS_VTV_MODEL", "eleven_english_sts_v2"),
        google_imagen_model=_get_secret_or_env("GOOGLE_IMAGEN_MODEL", "gemini-2.5-flash-image"),
        google_imagen_model_pro=_get_secret_or_env("GOOGLE_IMAGEN_MODEL_PRO", "gemini-3-pro-image-preview"),
        google_imagen_model_2=_get_secret_or_env("GOOGLE_IMAGEN_MODEL_2", "gemini-3.1-flash-image-preview"),
        google_veo_model=_get_secret_or_env("GOOGLE_VEO_MODEL", "veo-3.1-generate-preview"),
        grok_model=_get_secret_or_env("GROK_MODEL", "grok-imagine-video"),

        runs_db_path=_get_secret_or_env("RUNS_DB_PATH", "runs.db"),
        user_max_concurrency=int(_get_secret_or_env("USER_MAX_CONCURRENCY", "1") or "1"),
        global_max_concurrency=int(_get_secret_or_env("GLOBAL_MAX_CONCURRENCY", "4") or "4"),

        active_job_ttl_sec=int(_get_secret_or_env("ACTIVE_JOB_TTL_SEC", str(20 * 60)) or str(20 * 60)),

        enabled_tabs_default=enabled_tabs_default,
        enabled_tabs_by_school=enabled_tabs_by_school,

        tenant_config_dir=tenant_config_dir,

        suno_accounts_json=_get_secret_or_env("SUNO_ACCOUNTS_JSON", "[]"),

        vertex_sa_json=_get_secret_or_env("VERTEX_SA_JSON", ""),
        vertex_location=_get_secret_or_env("VERTEX_LOCATION", "us-central1"),

        gcs_bucket_name=_get_secret_or_env("GCS_BUCKET_NAME", ""),

        debug_auth=debug_auth,
    )


def ensure_session_ids():
    if "session_id" not in st.session_state:
        st.session_state.session_id = str(uuid.uuid4())
    if "user_id" not in st.session_state:
        st.session_state.user_id = "guest"
    if "school_id" not in st.session_state:
        st.session_state.school_id = "default"
