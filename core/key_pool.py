import json
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple, Callable

from core.config import AppConfig
from core.database import get_db, throttled_sync, force_sync


# ---------- time helpers ----------
def now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"

def minute_bucket_iso(dt: Optional[datetime] = None) -> str:
    dt = dt or datetime.utcnow()
    dt = dt.replace(second=0, microsecond=0)
    return dt.isoformat() + "Z"

def _seconds_to_next_minute(dt: Optional[datetime] = None) -> int:
    dt = dt or datetime.utcnow()
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
            throttled_sync()  # 트랜잭션 커밋 후 Turso 동기화
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

    # 기존 코드 호환: cfg의 단일 키를 1개짜리 풀로 변환
    spec: Dict[str, List[Dict[str, Any]]] = {}
    if cfg.openai_api_key:
        spec["openai"] = [{
            "name": "openai-fallback",
            "api_key": cfg.openai_api_key,
            "concurrency_limit": 3,
            "rpm_limit": 60,
            "priority": 0,
            "tenant_scope": "*",
            "is_active": True,
        }]
    if cfg.elevenlabs_api_key:
        spec["elevenlabs"] = [{
            "name": "elevenlabs-fallback",
            "api_key": cfg.elevenlabs_api_key,
            "concurrency_limit": 2,
            "rpm_limit": 30,
            "priority": 0,
            "tenant_scope": "*",
            "is_active": True,
        }]
    if cfg.google_api_key:
        spec["google_imagen"] = [{
            "name": "google-imagen-fallback",
            "api_key": cfg.google_api_key,
            "concurrency_limit": 2,
            "rpm_limit": 30,
            "priority": 0,
            "tenant_scope": "*",
            "is_active": True,
        }]
    return spec

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

                priority = int(item.get("priority") or 0)
                tenant_scope = (item.get("tenant_scope") or "*").strip()
                is_active = 1 if bool(item.get("is_active", True)) else 0
                expires_at = item.get("expires_at")

                # provider별 payload 구성
                if provider in ("openai", "midjourney", "elevenlabs", "google_imagen"):
                    api_key = (item.get("api_key") or "").strip()
                    if not api_key:
                        continue
                    payload = {"api_key": api_key}
                elif provider == "kling":
                    ak = (item.get("access_key") or "").strip()
                    sk = (item.get("secret_key") or "").strip()
                    if not (ak and sk):
                        continue
                    payload = {"access_key": ak, "secret_key": sk}
                else:
                    # 확장 provider: item에 key_payload를 직접 넣는 방식을 허용
                    payload = item.get("key_payload")
                    if not isinstance(payload, dict):
                        continue

                cur.execute("""
                    INSERT INTO api_keys
                      (provider, key_name, key_payload, concurrency_limit, rpm_limit, priority,
                       tenant_scope, is_active, expires_at, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(provider, key_name) DO UPDATE SET
                      key_payload=excluded.key_payload,
                      concurrency_limit=excluded.concurrency_limit,
                      rpm_limit=excluded.rpm_limit,
                      priority=excluded.priority,
                      tenant_scope=excluded.tenant_scope,
                      is_active=excluded.is_active,
                      expires_at=excluded.expires_at,
                      updated_at=excluded.updated_at
                """, (
                    provider, name, json.dumps(payload),
                    max(1, concurrency_limit),
                    rpm_limit,
                    priority,
                    tenant_scope,
                    is_active,
                    expires_at,
                    now, now
                ))
    conn.close()


# ---------- cleanup ----------
def cleanup_orphan_leases(cfg: AppConfig, lease_ttl_sec: Optional[int] = None) -> None:
    """
    서버 다운/예외로 release 못한 lease를 TTL로 만료 처리.
    """
    ttl = int(lease_ttl_sec or cfg.active_job_ttl_sec or 120)
    cutoff = (datetime.utcnow() - timedelta(seconds=ttl)).isoformat() + "Z"

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
            old_cut = (datetime.utcnow() - timedelta(minutes=30)).replace(second=0, microsecond=0).isoformat() + "Z"
            cur.execute("""
                DELETE FROM api_key_usage_minute
                WHERE minute_bucket < ?
            """, (old_cut,))

            # 오래된 waiters 정리 (6시간 이상 waiting이면 expired)
            w_cut = (datetime.utcnow() - timedelta(hours=6)).isoformat() + "Z"
            cur.execute("""
                UPDATE api_key_waiters
                SET state='expired', updated_at=?
                WHERE state='waiting' AND enqueued_at < ?
            """, (now_iso(), w_cut))
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
                     lease_cutoff_iso: str, bucket: str, request_units: int):
    # active leases count
    # usage count in current minute bucket
    cur.execute(f"""
        WITH active AS (
          SELECT api_key_id, COUNT(*) AS active_count
          FROM api_key_leases
          WHERE provider=? AND state='active' AND last_heartbeat_at >= ?
          GROUP BY api_key_id
        ),
        usage AS (
          SELECT api_key_id, count AS rpm_count
          FROM api_key_usage_minute
          WHERE minute_bucket=?
        )
        SELECT
          k.api_key_id, k.provider, k.key_name, k.key_payload,
          k.concurrency_limit, k.rpm_limit, k.priority, k.tenant_scope,
          COALESCE(a.active_count, 0) AS active_count,
          COALESCE(u.rpm_count, 0) AS rpm_count
        FROM api_keys k
        LEFT JOIN active a ON a.api_key_id = k.api_key_id
        LEFT JOIN usage  u ON u.api_key_id = k.api_key_id
        WHERE k.provider=?
          AND k.is_active=1
          AND (k.expires_at IS NULL OR k.expires_at='' OR k.expires_at > ?)
          AND COALESCE(a.active_count, 0) < k.concurrency_limit
          AND (
              k.rpm_limit IS NULL OR k.rpm_limit <= 0
              OR (COALESCE(u.rpm_count, 0) + ?) <= k.rpm_limit
          )
          AND (
              k.tenant_scope IS NULL OR k.tenant_scope='' OR k.tenant_scope='*'
              OR instr(',' || k.tenant_scope || ',', ',' || ? || ',') > 0
          )
        ORDER BY
          (1.0 * COALESCE(a.active_count,0) / k.concurrency_limit) ASC,
          (CASE WHEN k.rpm_limit IS NULL OR k.rpm_limit <= 0 THEN 0
                ELSE 1.0 * COALESCE(u.rpm_count,0) / k.rpm_limit END) ASC,
          k.priority DESC,
          k.api_key_id ASC
        LIMIT 1
    """, (provider, lease_cutoff_iso, bucket, provider, now_iso(), request_units, school_id))
    return cur.fetchone()

def _diagnose_block_reason(
    cur,
    provider: str,
    school_id: str,
    lease_cutoff_iso: str,
    bucket: str,
    request_units: int,
) -> Dict[str, Any]:
    """
    _select_best_key()가 None일 때, 막힌 원인이
    - rpm 때문인지
    - concurrency 때문인지
    - scope/만료/비활성 등으로 '키 자체가 없는지'
    를 최소 비용으로 판별.
    """
    cur.execute("""
        WITH active AS (
          SELECT api_key_id, COUNT(*) AS active_count
          FROM api_key_leases
          WHERE provider=? AND state='active' AND last_heartbeat_at >= ?
          GROUP BY api_key_id
        ),
        usage AS (
          SELECT api_key_id, count AS rpm_count
          FROM api_key_usage_minute
          WHERE minute_bucket=?
        )
        SELECT
          COUNT(*) AS total_keys,
          SUM(CASE WHEN COALESCE(a.active_count, 0) < k.concurrency_limit THEN 1 ELSE 0 END) AS conc_ok,
          SUM(CASE WHEN
                (k.rpm_limit IS NULL OR k.rpm_limit <= 0 OR (COALESCE(u.rpm_count, 0) + ?) <= k.rpm_limit)
              THEN 1 ELSE 0 END) AS rpm_ok,
          SUM(CASE WHEN
                COALESCE(a.active_count, 0) < k.concurrency_limit
                AND (k.rpm_limit IS NULL OR k.rpm_limit <= 0 OR (COALESCE(u.rpm_count, 0) + ?) <= k.rpm_limit)
              THEN 1 ELSE 0 END) AS both_ok
        FROM api_keys k
        LEFT JOIN active a ON a.api_key_id = k.api_key_id
        LEFT JOIN usage  u ON u.api_key_id = k.api_key_id
        WHERE k.provider=?
          AND k.is_active=1
          AND (k.expires_at IS NULL OR k.expires_at='' OR k.expires_at > ?)
          AND (
              k.tenant_scope IS NULL OR k.tenant_scope='' OR k.tenant_scope='*'
              OR instr(',' || k.tenant_scope || ',', ',' || ? || ',') > 0
          )
    """, (
        provider, lease_cutoff_iso, bucket,
        int(request_units), int(request_units),
        provider, now_iso(), school_id
    ))
    r = cur.fetchone()
    if not r:
        return {"blocked_by": "no_keys", "total_keys": 0, "conc_ok": 0, "rpm_ok": 0, "both_ok": 0}

    total_keys = int(r["total_keys"] or 0)
    conc_ok = int(r["conc_ok"] or 0)
    rpm_ok = int(r["rpm_ok"] or 0)
    both_ok = int(r["both_ok"] or 0)

    blocked_by = "mixed"
    if total_keys <= 0:
        blocked_by = "no_keys"
    elif both_ok > 0:
        blocked_by = "none"  # 이 케이스면 원래 _select_best_key가 뽑혔어야 함
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
        "both_ok": both_ok,
    }


def _inc_rpm(cur, api_key_id: int, bucket: str, units: int) -> None:
    cur.execute("""
        INSERT INTO api_key_usage_minute(api_key_id, minute_bucket, count)
        VALUES (?, ?, ?)
        ON CONFLICT(api_key_id, minute_bucket) DO UPDATE SET
          count = count + excluded.count
    """, (api_key_id, bucket, int(units)))

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
) -> Lease:
    """
    - FIFO 대기열(api_key_waiters) 기반으로 '내 차례'가 오면 키를 할당
    - 할당 시 concurrency + rpm 여유를 동시에 만족하는 "덜 막힌 키"를 선택
    - 할당 즉시 rpm을 request_units 만큼 consume (보통 submit 1회)
    """
    ttl = int(lease_ttl_sec or cfg.active_job_ttl_sec or 120)
    deadline = time.time() + float(max_wait_sec)

    conn = get_db(cfg)
    try:
        while True:
            now = datetime.utcnow()
            lease_cutoff = (now - timedelta(seconds=ttl)).isoformat() + "Z"
            bucket = minute_bucket_iso(now)

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
                        # 내 차례(또는 대기열 없음) → 키 선택 시도
                        row = _select_best_key(cur, provider, school_id, lease_cutoff, bucket, int(request_units))
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

                            # waiter 상태 갱신
                            cur.execute("""
                                UPDATE api_key_waiters
                                SET state='acquired', updated_at=?
                                WHERE provider=? AND run_id=? AND state='waiting'
                            """, (t, provider, run_id))

                            cur.execute("DELETE FROM api_key_waiters WHERE provider=? AND run_id=?", (provider, run_id))

                            payload = json.loads(row["key_payload"])
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
                            diag = _diagnose_block_reason(cur, provider, school_id, lease_cutoff, bucket, int(request_units))
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
                    row = _select_best_key(cur, provider, school_id, lease_cutoff, bucket, int(request_units))
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
                        payload = json.loads(row["key_payload"])
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
    conn = get_db(cfg)
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
    conn = get_db(cfg)
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
    conn = get_db(cfg)
    try:
        while True:
            b = minute_bucket_iso()
            now_dt = datetime.utcnow()
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
    force_sync()  # 키 풀 초기화 결과를 Turso에 즉시 반영
    _BOOTSTRAPPED = True
