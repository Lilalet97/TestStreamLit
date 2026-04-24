import json
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple, Callable

from core.config import AppConfig
from core.database import get_db, get_db_isolated


# ---------- time helpers ----------
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

def minute_bucket_iso(dt: Optional[datetime] = None) -> str:
    dt = dt or datetime.now(timezone.utc)
    dt = dt.replace(second=0, microsecond=0)
    return dt.isoformat().replace("+00:00", "Z")

def day_bucket_iso(dt: Optional[datetime] = None) -> str:
    dt = dt or datetime.now(timezone.utc)
    return dt.strftime("%Y-%m-%d")

def _seconds_to_next_minute(dt: Optional[datetime] = None) -> int:
    dt = dt or datetime.now(timezone.utc)
    next_min = dt.replace(second=0, microsecond=0) + timedelta(minutes=1)
    return max(1, int((next_min - dt).total_seconds()))

class Txn:
    def __init__(self, conn):
        self.conn = conn
    def __enter__(self):
        self.conn.execute("BEGIN IMMEDIATE;")
        return self.conn
    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.conn.execute("COMMIT;")
            pass
        else:
            self.conn.execute("ROLLBACK;")
        return False


# ---------- models ----------
@dataclass
class Lease:
    lease_id: str
    api_key_id: int
    provider: str
    key_name: str
    key_payload: Dict[str, Any]
    acquired_at: str
    ttl_sec: int

    def __repr__(self):
        return f"Lease(lease_id={self.lease_id!r}, api_key_id={self.api_key_id}, provider={self.provider!r}, key_name={self.key_name!r}, key_payload=<REDACTED>)"


# ---------- config/seeding ----------
def _get_secret_or_env(key: str, default: str = "") -> str:
    # streamlit.secrets를 직접 참조하지 않고(모듈 독립성), env/secrets.toml 둘 다 지원하는 형태로 유지
    # secrets.toml 값은 보통 load_config에서 cfg로 들어오므로, 여기서는 env fallback만 둠.
    return (os.getenv(key, default) or "").strip()

def load_key_pool_spec(cfg: AppConfig) -> Dict[str, List[Dict[str, Any]]]:
    """
    우선순위:
      1) KEY_POOL_JSON env (운영에서 추천)
      2) cfg에 있는 단일 키(기존 호환) -> provider당 1개 엔트리 자동 구성
    """
    raw = _get_secret_or_env("KEY_POOL_JSON", "")
    if raw:
        try:
            j = json.loads(raw)
            if isinstance(j, dict):
                out: Dict[str, List[Dict[str, Any]]] = {}
                for provider, arr in j.items():
                    if isinstance(provider, str) and isinstance(arr, list):
                        out[provider] = [x for x in arr if isinstance(x, dict)]
                return out
        except Exception:
            pass

    # KEY_POOL_JSON이 없으면 빈 스펙 반환
    return {}

def ensure_tables(cfg: AppConfig) -> None:
    conn = get_db(cfg)
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS api_keys (
      api_key_id INTEGER PRIMARY KEY AUTOINCREMENT,
      provider TEXT NOT NULL,
      key_name TEXT NOT NULL,
      key_payload TEXT NOT NULL,
      concurrency_limit INTEGER NOT NULL DEFAULT 1,
      rpm_limit INTEGER,
      priority INTEGER NOT NULL DEFAULT 0,
      tenant_scope TEXT,
      is_active INTEGER NOT NULL DEFAULT 1,
      expires_at TEXT,
      created_at TEXT,
      updated_at TEXT,
      UNIQUE(provider, key_name)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS api_key_leases (
      lease_id TEXT PRIMARY KEY,
      api_key_id INTEGER NOT NULL,
      provider TEXT NOT NULL,
      run_id TEXT NOT NULL,
      user_id TEXT,
      session_id TEXT,
      school_id TEXT,
      state TEXT NOT NULL,
      acquired_at TEXT NOT NULL,
      last_heartbeat_at TEXT NOT NULL,
      released_at TEXT,
      ttl_sec INTEGER NOT NULL DEFAULT 120,
      FOREIGN KEY(api_key_id) REFERENCES api_keys(api_key_id)
    )
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_api_key_leases_provider_state
    ON api_key_leases(provider, state, last_heartbeat_at)
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS api_key_usage_minute (
      api_key_id INTEGER NOT NULL,
      minute_bucket TEXT NOT NULL,
      count INTEGER NOT NULL,
      PRIMARY KEY(api_key_id, minute_bucket),
      FOREIGN KEY(api_key_id) REFERENCES api_keys(api_key_id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS api_key_usage_daily (
      api_key_id INTEGER NOT NULL,
      model TEXT NOT NULL,
      day_bucket TEXT NOT NULL,
      count INTEGER NOT NULL DEFAULT 0,
      PRIMARY KEY(api_key_id, model, day_bucket),
      FOREIGN KEY(api_key_id) REFERENCES api_keys(api_key_id)
    )
    """)

    # rpd_limits 컬럼 마이그레이션
    try:
        cur.execute("ALTER TABLE api_keys ADD COLUMN rpd_limits TEXT")
    except Exception:
        pass

    cur.execute("""
    CREATE TABLE IF NOT EXISTS api_key_waiters (
      waiter_id TEXT PRIMARY KEY,
      provider TEXT NOT NULL,
      run_id TEXT NOT NULL,
      user_id TEXT,
      session_id TEXT,
      school_id TEXT,
      enqueued_at TEXT NOT NULL,
      state TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      UNIQUE(provider, run_id)
    )
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_api_key_waiters_provider_state
    ON api_key_waiters(provider, state, enqueued_at)
    """)

    conn.commit()
    conn.close()

def seed_keys(cfg: AppConfig) -> None:
    """
    KEY_POOL_JSON(또는 cfg 단일키 fallback)에서 api_keys 테이블을 upsert.
    """
    spec = load_key_pool_spec(cfg)
    if not spec:
        return

    conn = get_db(cfg)
    now = now_iso()
    with Txn(conn):
        cur = conn.cursor()
        for provider, items in spec.items():
            for item in items:
                name = (item.get("name") or "").strip()
                if not name:
                    continue

                concurrency_limit = int(item.get("concurrency_limit") or 1)
                rpm_limit = item.get("rpm_limit")
                rpm_limit = None if rpm_limit is None else int(rpm_limit)
                rpd_limits = item.get("rpd_limits")  # dict: model -> daily limit
                rpd_limits_json = json.dumps(rpd_limits) if isinstance(rpd_limits, dict) else None

                priority = int(item.get("priority") or 0)
                tenant_scope = (item.get("tenant_scope") or "*").strip()
                is_active = 1 if bool(item.get("is_active", True)) else 0
                expires_at = item.get("expires_at")

                # provider별 payload 구성
                if provider in ("openai", "elevenlabs",
                                "google_imagen", "google_veo", "grok",
                                "ltx_video"):
                    api_key = (item.get("api_key") or "").strip()
                    if not api_key:
                        continue
                    payload = {"api_key": api_key}
                elif provider == "midjourney":
                    api_key = (item.get("api_key") or "").strip()
                    if not api_key:
                        continue
                    payload = {"api_key": api_key, "channel": (item.get("channel") or "").strip()}
                    # [VERTEX AI] sa_json 기반 payload — 결제 등록 후 복원
                    # if provider in ("google_imagen", "google_veo"):
                    #     sa_json = (item.get("sa_json") or "").strip()
                    #     ...
                elif provider == "kling":
                    ak = (item.get("access_key") or "").strip()
                    sk = (item.get("secret_key") or "").strip()
                    if not (ak and sk):
                        continue
                    payload = {"access_key": ak, "secret_key": sk}
                else:
                    payload = item.get("key_payload")
                    if not isinstance(payload, dict):
                        continue

                cur.execute("""
                    INSERT INTO api_keys
                      (provider, key_name, key_payload, concurrency_limit, rpm_limit, rpd_limits, priority,
                       tenant_scope, is_active, expires_at, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(provider, key_name) DO UPDATE SET
                      key_payload=excluded.key_payload,
                      concurrency_limit=excluded.concurrency_limit,
                      rpm_limit=excluded.rpm_limit,
                      rpd_limits=excluded.rpd_limits,
                      priority=excluded.priority,
                      tenant_scope=excluded.tenant_scope,
                      expires_at=excluded.expires_at,
                      updated_at=excluded.updated_at
                """, (
                    provider, name, json.dumps(payload),
                    max(1, concurrency_limit),
                    rpm_limit,
                    rpd_limits_json,
                    priority,
                    tenant_scope,
                    is_active,
                    expires_at,
                    now, now
                ))
        # secrets에서 제거된 키를 비활성화
        all_names = []
        for provider, items in spec.items():
            for item in items:
                name = (item.get("name") or "").strip()
                if name:
                    all_names.append((provider, name))
        if all_names:
            cur.execute("SELECT provider, key_name FROM api_keys WHERE is_active = 1")
            for row in cur.fetchall():
                if (row["provider"], row["key_name"]) not in all_names:
                    cur.execute(
                        "UPDATE api_keys SET is_active = 0, updated_at = ? "
                        "WHERE provider = ? AND key_name = ?",
                        (now, row["provider"], row["key_name"]),
                    )

    conn.close()


# ---------- cleanup ----------
def cleanup_orphan_leases(cfg: AppConfig, lease_ttl_sec: Optional[int] = None) -> None:
    """
    서버 다운/예외로 release 못한 lease를 TTL로 만료 처리.
    """
    ttl = int(lease_ttl_sec or cfg.active_job_ttl_sec or 120)
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=ttl)).isoformat().replace("+00:00", "Z")

    conn = get_db(cfg)
    try:
        with Txn(conn):
            cur = conn.cursor()
            cur.execute("""
                UPDATE api_key_leases
                SET state='expired', released_at=?
                WHERE state='active'
                  AND (last_heartbeat_at IS NULL OR last_heartbeat_at = '' OR last_heartbeat_at < ?)
            """, (now_iso(), cutoff))

            # 오래된 minute bucket 정리 (30분 이상)
            old_cut = (datetime.now(timezone.utc) - timedelta(minutes=30)).replace(second=0, microsecond=0).isoformat().replace("+00:00", "Z")
            cur.execute("""
                DELETE FROM api_key_usage_minute
                WHERE minute_bucket < ?
            """, (old_cut,))

            # 오래된 waiters 정리 (6시간 이상 waiting이면 expired)
            w_cut = (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat().replace("+00:00", "Z")
            cur.execute("""
                UPDATE api_key_waiters
                SET state='expired', updated_at=?
                WHERE state='waiting' AND enqueued_at < ?
            """, (now_iso(), w_cut))

            # 오래된 daily bucket 정리 (7일 이상)
            old_day = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
            cur.execute("""
                DELETE FROM api_key_usage_daily
                WHERE day_bucket < ?
            """, (old_day,))
    finally:
        conn.close()


# ---------- queue + acquire/release ----------
def _ensure_waiter(cur, provider: str, run_id: str,
                   user_id: str, session_id: str, school_id: str) -> str:
    waiter_id = str(uuid.uuid4())
    t = now_iso()
    cur.execute("""
        INSERT INTO api_key_waiters
          (waiter_id, provider, run_id, user_id, session_id, school_id, enqueued_at, state, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'waiting', ?)
        ON CONFLICT(provider, run_id) DO UPDATE SET
          updated_at=excluded.updated_at
    """, (waiter_id, provider, run_id, user_id, session_id, school_id, t, t))

    # 기존 row의 waiter_id를 그대로 쓰고 싶으면 조회
    cur.execute("SELECT waiter_id FROM api_key_waiters WHERE provider=? AND run_id=?", (provider, run_id))
    row = cur.fetchone()
    return str(row["waiter_id"]) if row else waiter_id

def _waiter_head_and_pos(cur, provider: str, run_id: str) -> Tuple[Optional[str], Optional[int]]:
    cur.execute("""
        SELECT run_id
        FROM api_key_waiters
        WHERE provider=? AND state='waiting'
        ORDER BY enqueued_at ASC
        LIMIT 1
    """, (provider,))
    head = cur.fetchone()
    head_run = str(head["run_id"]) if head else None

    cur.execute("""
        SELECT enqueued_at FROM api_key_waiters
        WHERE provider=? AND run_id=?
    """, (provider, run_id))
    me = cur.fetchone()
    if not me:
        return head_run, None
    me_t = str(me["enqueued_at"])

    cur.execute("""
        SELECT COUNT(*) AS c
        FROM api_key_waiters
        WHERE provider=? AND state='waiting' AND enqueued_at <= ?
    """, (provider, me_t))
    pos = int(cur.fetchone()["c"])
    return head_run, pos

def _select_best_key(cur, provider: str, school_id: str,
                     lease_cutoff_iso: str, bucket: str, request_units: int,
                     excluded_key_ids: Optional[set] = None):
    import logging as _lg

    # Phase 1: 단순 쿼리로 후보 키 목록 가져오기
    excl = excluded_key_ids or set()
    excl_list = list(excl) if excl else [0]
    excl_ph = ",".join("?" for _ in excl_list)

    cur.execute(f"""
        SELECT api_key_id, provider, key_name, key_payload,
               concurrency_limit, rpm_limit, priority, tenant_scope, rpd_limits
        FROM api_keys
        WHERE provider=? AND is_active=1
          AND api_key_id NOT IN ({excl_ph})
          AND (expires_at IS NULL OR expires_at='' OR expires_at > ?)
          AND (tenant_scope IS NULL OR tenant_scope='' OR tenant_scope='*'
               OR instr(',' || tenant_scope || ',', ',' || ? || ',') > 0)
        ORDER BY priority DESC, api_key_id ASC
    """, (provider, *excl_list, now_iso(), school_id))
    candidates = cur.fetchall()

    if not candidates:
        return None

    # Phase 2: 각 후보에 대해 concurrency + RPM 체크
    for row in candidates:
        kid = row["api_key_id"] if isinstance(row, dict) else row[0]
        climit = row["concurrency_limit"] if isinstance(row, dict) else row[4]
        rlimit = row["rpm_limit"] if isinstance(row, dict) else row[5]

        # active lease 수
        cur.execute(
            "SELECT COUNT(*) FROM api_key_leases WHERE api_key_id=? AND state='active' AND last_heartbeat_at >= ?",
            (kid, lease_cutoff_iso))
        ac_row = cur.fetchone()
        ac = ac_row[0] if isinstance(ac_row, (tuple, list)) else (ac_row.get("COUNT(*)", 0) if isinstance(ac_row, dict) else 0)

        if ac >= climit:
            continue

        # RPM 체크
        if rlimit and int(rlimit) > 0:
            cur.execute(
                "SELECT count FROM api_key_usage_minute WHERE api_key_id=? AND minute_bucket=?",
                (kid, bucket))
            rpm_row = cur.fetchone()
            rpm = 0
            if rpm_row:
                rpm = rpm_row[0] if isinstance(rpm_row, (tuple, list)) else (rpm_row.get("count", 0) if isinstance(rpm_row, dict) else 0)
            if (rpm + int(request_units)) > int(rlimit):
                continue

        # 통과 → active_count, rpm_count 추가
        if isinstance(row, dict):
            row["active_count"] = ac
            row["rpm_count"] = rpm if (rlimit and int(rlimit) > 0) else 0
        else:
            # tuple인 경우 dict로 변환
            cols = ["api_key_id", "provider", "key_name", "key_payload",
                    "concurrency_limit", "rpm_limit", "priority", "tenant_scope", "rpd_limits"]
            row = dict(zip(cols, row))
            row["active_count"] = ac
            row["rpm_count"] = 0

        return row

    return None

def _diagnose_block_reason(
    cur,
    provider: str,
    school_id: str,
    lease_cutoff_iso: str,
    bucket: str,
    request_units: int,
    *,
    model: Optional[str] = None,
    day: Optional[str] = None,
) -> Dict[str, Any]:
    """
    _select_best_key()가 None일 때, 막힌 원인 판별.
    CTE 대신 단순 쿼리 + Python 루프.
    """
    cur.execute("""
        SELECT api_key_id, concurrency_limit, rpm_limit, rpd_limits
        FROM api_keys
        WHERE provider=? AND is_active=1
          AND (expires_at IS NULL OR expires_at='' OR expires_at > ?)
          AND (tenant_scope IS NULL OR tenant_scope='' OR tenant_scope='*'
               OR instr(',' || tenant_scope || ',', ',' || ? || ',') > 0)
    """, (provider, now_iso(), school_id))
    keys = cur.fetchall()
    if not keys:
        return {"blocked_by": "no_keys", "total_keys": 0, "conc_ok": 0, "rpm_ok": 0, "rpd_ok": 0, "both_ok": 0}

    total_keys = len(keys)
    conc_ok = 0
    rpm_ok = 0
    rpd_ok = 0
    all_ok = 0

    _day = day or day_bucket_iso()
    for k in keys:
        kid = k["api_key_id"] if isinstance(k, dict) else k[0]
        climit = k["concurrency_limit"] if isinstance(k, dict) else k[1]
        rlimit = k["rpm_limit"] if isinstance(k, dict) else k[2]
        rpd_lim_raw = k["rpd_limits"] if isinstance(k, dict) else k[3]

        # concurrency 체크
        cur.execute(
            "SELECT COUNT(*) FROM api_key_leases WHERE api_key_id=? AND state='active' AND last_heartbeat_at >= ?",
            (kid, lease_cutoff_iso))
        ac_row = cur.fetchone()
        ac = ac_row[0] if isinstance(ac_row, (tuple, list)) else (ac_row.get("COUNT(*)", 0) if isinstance(ac_row, dict) else 0)
        c_ok = ac < climit

        # RPM 체크
        r_ok = True
        if rlimit and int(rlimit) > 0:
            cur.execute(
                "SELECT count FROM api_key_usage_minute WHERE api_key_id=? AND minute_bucket=?",
                (kid, bucket))
            rpm_row = cur.fetchone()
            rpm = 0
            if rpm_row:
                rpm = rpm_row[0] if isinstance(rpm_row, (tuple, list)) else (rpm_row.get("count", 0) if isinstance(rpm_row, dict) else 0)
            r_ok = (rpm + int(request_units)) <= int(rlimit)

        # RPD 체크
        d_ok = True
        if model:
            rpd_limit = _get_rpd_limit(rpd_lim_raw, model)
            if rpd_limit is not None:
                rpd_count = _get_rpd_count(cur, int(kid), model, _day)
                if rpd_count + int(request_units) > rpd_limit:
                    d_ok = False

        if c_ok:
            conc_ok += 1
        if r_ok:
            rpm_ok += 1
        if d_ok:
            rpd_ok += 1
        if c_ok and r_ok and d_ok:
            all_ok += 1

    blocked_by = "mixed"
    if total_keys <= 0:
        blocked_by = "no_keys"
    elif all_ok > 0:
        blocked_by = "none"
    elif rpd_ok == 0 and conc_ok > 0 and rpm_ok > 0:
        blocked_by = "rpd"
    elif conc_ok > 0 and rpm_ok == 0:
        blocked_by = "rpm"
    elif conc_ok == 0 and rpm_ok > 0:
        blocked_by = "concurrency"
    elif conc_ok == 0 and rpm_ok == 0:
        blocked_by = "concurrency_and_rpm"

    return {
        "blocked_by": blocked_by,
        "total_keys": total_keys,
        "conc_ok": conc_ok,
        "rpm_ok": rpm_ok,
        "rpd_ok": rpd_ok,
        "both_ok": all_ok,
    }


def _inc_rpm(cur, api_key_id: int, bucket: str, units: int) -> None:
    cur.execute("""
        INSERT INTO api_key_usage_minute(api_key_id, minute_bucket, count)
        VALUES (?, ?, ?)
        ON CONFLICT(api_key_id, minute_bucket) DO UPDATE SET
          count = count + excluded.count
    """, (api_key_id, bucket, int(units)))


def _inc_rpd(cur, api_key_id: int, model: str, day: str, units: int) -> None:
    """일별 사용량 증가."""
    cur.execute("""
        INSERT INTO api_key_usage_daily(api_key_id, model, day_bucket, count)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(api_key_id, model, day_bucket) DO UPDATE SET
          count = count + excluded.count
    """, (api_key_id, model, day, int(units)))


def _get_rpd_count(cur, api_key_id: int, model: str, day: str) -> int:
    """특정 키·모델의 오늘 사용량 조회."""
    cur.execute("""
        SELECT count FROM api_key_usage_daily
        WHERE api_key_id=? AND model=? AND day_bucket=?
    """, (api_key_id, model, day))
    r = cur.fetchone()
    return int(r["count"]) if r else 0


def _get_rpd_limit(rpd_limits_json: Optional[str], model: str) -> Optional[int]:
    """rpd_limits JSON에서 모델별 일일 한도 추출. None = 무제한."""
    if not rpd_limits_json:
        return None
    try:
        limits = json.loads(rpd_limits_json)
        if not isinstance(limits, dict):
            return None
        val = limits.get(model)
        if val is None:
            return None
        limit = int(val)
        return limit if limit > 0 else None  # 0 이하 = 무제한 (RPM과 동일)
    except Exception:
        return None


def acquire_lease(
    cfg: AppConfig,
    provider: str,
    run_id: str,
    user_id: str,
    session_id: str,
    school_id: str,
    *,
    wait: bool = True,
    max_wait_sec: int = 60,
    poll_interval_sec: float = 1.0,
    lease_ttl_sec: Optional[int] = None,
    request_units: int = 1,
    on_wait: Optional[Callable[[Dict[str, Any]], None]] = None,
    model: Optional[str] = None,
) -> Lease:
    """
    - FIFO 대기열(api_key_waiters) 기반으로 '내 차례'가 오면 키를 할당
    - 할당 시 concurrency + rpm 여유를 동시에 만족하는 "덜 막힌 키"를 선택
    - 할당 즉시 rpm을 request_units 만큼 consume (보통 submit 1회)
    """
    ttl = int(lease_ttl_sec or cfg.active_job_ttl_sec or 120)
    deadline = time.time() + float(max_wait_sec)

    conn = get_db_isolated(cfg)
    try:
        while True:
            now = datetime.now(timezone.utc)
            lease_cutoff = (now - timedelta(seconds=ttl)).isoformat().replace("+00:00", "Z")
            bucket = minute_bucket_iso(now)
            day = day_bucket_iso(now)

            wait_info = None

            with Txn(conn):
                cur = conn.cursor()

                # stale lease 먼저 무시되게 처리(선택 쿼리에서 cutoff로 배제되지만, 상태도 정리)
                cur.execute("""
                    UPDATE api_key_leases
                    SET state='expired', released_at=?
                    WHERE state='active'
                      AND (last_heartbeat_at IS NULL OR last_heartbeat_at='' OR last_heartbeat_at < ?)
                """, (now_iso(), lease_cutoff))

                # stale waiter 정리: max_wait_sec(60s) 초과 + 여유 → 90초 이상 된 waiting 삭제
                # Streamlit Cloud에서 이전 세션이 비정상 종료되어 남은 고아 waiter 방지
                _waiter_cutoff = (now - timedelta(seconds=90)).isoformat().replace("+00:00", "Z")
                cur.execute("""
                    DELETE FROM api_key_waiters
                    WHERE state='waiting'
                      AND updated_at < ?
                """, (_waiter_cutoff,))

                if wait:
                    _ensure_waiter(cur, provider, run_id, user_id, session_id, school_id)
                    head_run, pos = _waiter_head_and_pos(cur, provider, run_id)
                    if head_run is not None and head_run != run_id:
                        wait_info = {
                            "state": "waiting_turn",
                            "provider": provider,
                            "run_id": run_id,
                            "pos": pos,
                            "head_run": head_run,
                        }
                    else:
                        # 내 차례(또는 대기열 없음) → 키 선택 시도 (RPD 초과 키 제외 루프)
                        rpd_excluded: set = set()
                        row = None
                        while True:
                            row = _select_best_key(cur, provider, school_id, lease_cutoff, bucket,
                                                   int(request_units), excluded_key_ids=rpd_excluded)
                            if row is None:
                                break
                            # RPD 체크: model이 지정된 경우만
                            if model:
                                rpd_limit = _get_rpd_limit(row["rpd_limits"], model)
                                if rpd_limit is not None:
                                    rpd_count = _get_rpd_count(cur, int(row["api_key_id"]), model, day)
                                    if rpd_count + int(request_units) > rpd_limit:
                                        rpd_excluded.add(int(row["api_key_id"]))
                                        continue
                            break  # RPD OK 또는 무제한

                        if row is not None:
                            lease_id = str(uuid.uuid4())
                            t = now_iso()

                            cur.execute("""
                                INSERT INTO api_key_leases
                                  (lease_id, api_key_id, provider, run_id, user_id, session_id, school_id,
                                   state, acquired_at, last_heartbeat_at, ttl_sec)
                                VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
                            """, (
                                lease_id, int(row["api_key_id"]), provider, run_id, user_id, session_id, school_id,
                                t, t, ttl
                            ))

                            # submit 1회는 rpm에 반영(요청 단위)
                            _inc_rpm(cur, int(row["api_key_id"]), bucket, int(request_units))

                            # RPD 카운터 증가
                            if model:
                                _inc_rpd(cur, int(row["api_key_id"]), model, day, int(request_units))

                            # waiter 상태 갱신
                            cur.execute("""
                                UPDATE api_key_waiters
                                SET state='acquired', updated_at=?
                                WHERE provider=? AND run_id=? AND state='waiting'
                            """, (t, provider, run_id))

                            cur.execute("DELETE FROM api_key_waiters WHERE provider=? AND run_id=?", (provider, run_id))

                            try:
                                payload = json.loads(row["key_payload"])
                            except (ValueError, Exception):
                                payload = {}
                            return Lease(
                                lease_id=lease_id,
                                api_key_id=int(row["api_key_id"]),
                                provider=provider,
                                key_name=str(row["key_name"]),
                                key_payload=payload if isinstance(payload, dict) else {},
                                acquired_at=t,
                                ttl_sec=ttl,
                            )
                        else:
                            diag = _diagnose_block_reason(cur, provider, school_id, lease_cutoff, bucket,
                                                          int(request_units), model=model, day=day)
                            blocked_by = diag.get("blocked_by")

                            if blocked_by == "rpm":
                                wait_info = {
                                    "state": "waiting_rpm",
                                    "provider": provider,
                                    "run_id": run_id,
                                    "pos": pos,
                                    "retry_after_sec": _seconds_to_next_minute(now),
                                    **diag,
                                }
                            elif blocked_by == "rpd":
                                wait_info = {
                                    "state": "waiting_rpd",
                                    "provider": provider,
                                    "run_id": run_id,
                                    "pos": pos,
                                    "model": model,
                                    **diag,
                                }
                            else:
                                # concurrency / mixed / no_keys 등은 기존 waiting_key로 유지하되 reason을 붙임
                                wait_info = {
                                    "state": "waiting_key",
                                    "provider": provider,
                                    "run_id": run_id,
                                    "pos": pos,
                                    "reason": blocked_by,
                                    **diag,
                                }
                else:
                    # wait=False 경로: RPD 제외 루프
                    rpd_excluded: set = set()
                    row = None
                    while True:
                        row = _select_best_key(cur, provider, school_id, lease_cutoff, bucket,
                                               int(request_units), excluded_key_ids=rpd_excluded)
                        if row is None:
                            break
                        if model:
                            rpd_limit = _get_rpd_limit(row["rpd_limits"], model)
                            if rpd_limit is not None:
                                rpd_count = _get_rpd_count(cur, int(row["api_key_id"]), model, day)
                                if rpd_count + int(request_units) > rpd_limit:
                                    rpd_excluded.add(int(row["api_key_id"]))
                                    continue
                        break

                    if row is not None:
                        lease_id = str(uuid.uuid4())
                        t = now_iso()
                        cur.execute("""
                            INSERT INTO api_key_leases
                              (lease_id, api_key_id, provider, run_id, user_id, session_id, school_id,
                               state, acquired_at, last_heartbeat_at, ttl_sec)
                            VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
                        """, (
                            lease_id, int(row["api_key_id"]), provider, run_id, user_id, session_id, school_id,
                            t, t, ttl
                        ))
                        _inc_rpm(cur, int(row["api_key_id"]), bucket, int(request_units))
                        if model:
                            _inc_rpd(cur, int(row["api_key_id"]), model, day, int(request_units))
                        try:
                            payload = json.loads(row["key_payload"])
                        except (ValueError, Exception):
                            payload = {}
                        return Lease(
                            lease_id=lease_id,
                            api_key_id=int(row["api_key_id"]),
                            provider=provider,
                            key_name=str(row["key_name"]),
                            key_payload=payload if isinstance(payload, dict) else {},
                            acquired_at=t,
                            ttl_sec=ttl,
                        )
                    raise TimeoutError(f"[{provider}] 사용 가능한 키가 없습니다(wait=False).")

            # 여기로 오면: (wait=True && 내 차례 아님) 또는 (내 차례인데 키 없음)
            if wait_info is not None and on_wait:
                try:
                    on_wait(wait_info)
                except Exception:
                    # UI 콜백 실패로 key pool 로직이 깨지면 안 됨
                    pass
            
            if not wait:
                raise TimeoutError(f"[{provider}] 사용 가능한 키가 없습니다(wait=False).")
            if time.time() >= deadline:
                if wait:
                    try:
                        with Txn(conn):
                            conn.execute(
                                "DELETE FROM api_key_waiters WHERE provider=? AND run_id=?",
                                (provider, run_id),
                            )
                    except Exception:
                        pass
                # RPD 소진으로 대기 중이었는지 확인
                if wait_info and wait_info.get("state") == "waiting_rpd":
                    raise TimeoutError(f"[{provider}] 일일 요청 한도(RPD)에 도달했습니다.")
                raise TimeoutError(f"[{provider}] 키 대기 timeout ({max_wait_sec}s).")

            time.sleep(float(poll_interval_sec))
    finally:
        try:
            if wait:
                with Txn(conn):
                    conn.execute(
                        "DELETE FROM api_key_waiters WHERE provider=? AND run_id=?",
                        (provider, run_id),
                    )
        except Exception:
            pass
        conn.close()

def heartbeat(cfg: AppConfig, lease_id: str) -> None:
    conn = get_db_isolated(cfg)
    try:
        with Txn(conn):
            conn.execute("""
                UPDATE api_key_leases
                SET last_heartbeat_at=?
                WHERE lease_id=? AND state='active'
            """, (now_iso(), lease_id))
    finally:
        conn.close()

def release_lease(cfg: AppConfig, lease_id: str, state: str = "released") -> None:
    conn = get_db_isolated(cfg)
    try:
        with Txn(conn):
            conn.execute("""
                UPDATE api_key_leases
                SET state=?, released_at=?
                WHERE lease_id=? AND state='active'
            """, (state, now_iso(), lease_id))
    finally:
        conn.close()

def consume_rpm(cfg: AppConfig, api_key_id: int, units: int = 1, wait: bool = True,
                max_wait_sec: int = 30, poll_interval_sec: float = 1.0,
                on_wait: Optional[Callable[[Dict[str, Any]], None]] = None) -> None:

    deadline = time.time() + float(max_wait_sec)
    conn = get_db_isolated(cfg)
    try:
        while True:
            b = minute_bucket_iso()
            now_dt = datetime.now(timezone.utc)
            wait_info = None

            with Txn(conn):
                cur = conn.cursor()
                cur.execute("SELECT provider, rpm_limit FROM api_keys WHERE api_key_id=?", (api_key_id,))
                row = cur.fetchone()
                if not row:
                    return

                provider = str(row["provider"])
                rpm_limit = row["rpm_limit"]

                if rpm_limit is None or int(rpm_limit) <= 0:
                    _inc_rpm(cur, api_key_id, b, int(units))
                    return

                cur.execute("""
                    SELECT count FROM api_key_usage_minute
                    WHERE api_key_id=? AND minute_bucket=?
                """, (api_key_id, b))
                u = cur.fetchone()
                current = int(u["count"]) if u else 0

                if current + int(units) <= int(rpm_limit):
                    _inc_rpm(cur, api_key_id, b, int(units))
                    return

                # 여기서부터는 rpm_limit 때문에 막힘
                wait_info = {
                    "state": "waiting_rpm",
                    "provider": provider,
                    "api_key_id": api_key_id,
                    "bucket": b,
                    "current": current,
                    "rpm_limit": int(rpm_limit),
                    "units": int(units),
                    "retry_after_sec": _seconds_to_next_minute(now_dt),
                }

            # Txn 밖
            if not wait:
                raise TimeoutError("rpm_limit reached (wait=False)")
            if time.time() >= deadline:
                raise TimeoutError("rpm_limit wait timeout")

            if wait_info is not None and on_wait:
                try:
                    on_wait(wait_info)
                except Exception:
                    pass

            time.sleep(float(poll_interval_sec))
    finally:
        conn.close()


_BOOTSTRAPPED = False

def get_daily_usage_report(cfg: AppConfig, day: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    특정 날짜(기본 오늘)의 키별·모델별 일일 사용량 리포트.
    Returns: [{"key_name", "model", "count", "rpd_limit"}]
    """
    _day = day or day_bucket_iso()
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT k.key_name, k.rpd_limits, d.model, d.count
            FROM api_key_usage_daily d
            JOIN api_keys k ON k.api_key_id = d.api_key_id
            WHERE d.day_bucket = ?
            ORDER BY k.key_name, d.model
        """, (_day,))
        rows = cur.fetchall()
        result = []
        for r in rows:
            rpd_limit = _get_rpd_limit(r["rpd_limits"], r["model"])
            result.append({
                "key_name": r["key_name"],
                "model": r["model"],
                "count": int(r["count"]),
                "rpd_limit": rpd_limit,
            })
        return result
    finally:
        conn.close()


def bootstrap(cfg: AppConfig) -> None:
    """
    app.py에서 1회 호출하는 것을 권장.
    프로세스당 1회만 실행 (Streamlit rerun 시 스킵).
    """
    global _BOOTSTRAPPED
    if _BOOTSTRAPPED:
        return
    ensure_tables(cfg)
    seed_keys(cfg)
    cleanup_orphan_leases(cfg)
    _BOOTSTRAPPED = True
