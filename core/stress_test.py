# core/stress_test.py
"""부하 테스트 엔진 — Burst 모드 (barrier 동기화) + Plan 기반 순차 라운드.

Plan: provider별로 [5, 10, 15]명씩 동시 요청을 보내며
지연시간·실패율을 측정하고 비교 그래프를 생성한다.
"""
import json
import logging
import queue
import random
import threading
import time
import uuid
from dataclasses import dataclass, asdict, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from core.config import AppConfig
from core.database import get_db
from core.key_pool import acquire_lease, release_lease, Lease

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


# ── Plan config ──────────────────────────────────────────

@dataclass
class StressPlanConfig:
    """부하 테스트 플랜 설정."""
    providers: List[str] = field(default_factory=lambda: ["openai"])
    user_counts: List[int] = field(default_factory=lambda: [5, 10, 15])
    mock_mode: bool = True
    mock_latency_min_ms: int = 100
    mock_latency_max_ms: int = 500
    lease_wait_sec: int = 30
    lease_ttl_sec: int = 60

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, s: str) -> "StressPlanConfig":
        return cls(**json.loads(s))


# ── 키 풀 concurrency 임시 변경 ──────────────────────────

def _boost_concurrency(cfg: AppConfig, provider: str, new_limit: int) -> list[tuple]:
    """해당 provider의 키 concurrency_limit를 일시적으로 올린다.
    Returns: [(api_key_id, original_limit), ...]
    """
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT api_key_id, concurrency_limit FROM api_keys WHERE provider=?",
            (provider,),
        )
        originals = [(row["api_key_id"], row["concurrency_limit"]) for row in cur.fetchall()]
        cur.execute(
            "UPDATE api_keys SET concurrency_limit=? WHERE provider=?",
            (new_limit, provider),
        )
        conn.commit()
        return originals
    finally:
        conn.close()


def _restore_concurrency(cfg: AppConfig, originals: list[tuple]):
    """원래 concurrency_limit로 복원."""
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        for key_id, limit in originals:
            cur.execute(
                "UPDATE api_keys SET concurrency_limit=? WHERE api_key_id=?",
                (limit, key_id),
            )
        conn.commit()
    finally:
        conn.close()


# ── Burst worker ─────────────────────────────────────────

def _burst_worker(
    cfg: AppConfig,
    test_id: str,
    worker_id: int,
    provider: str,
    school_id: str,
    mock_mode: bool,
    mock_latency: tuple[int, int],
    lease_wait_sec: int,
    lease_ttl_sec: int,
    barrier: threading.Barrier,
    results_queue: queue.Queue,
):
    """Barrier 동기화 후 1회 요청: acquire → call → release."""
    user_id = f"__stress_{test_id[:8]}_{worker_id}"
    session_id = f"stress_sess_{worker_id}"
    run_id = str(uuid.uuid4())

    # 모든 워커가 준비될 때까지 대기
    try:
        barrier.wait(timeout=30)
    except threading.BrokenBarrierError:
        results_queue.put({
            "test_id": test_id, "worker_id": worker_id, "request_seq": 1,
            "started_at": _now_iso(), "finished_at": _now_iso(),
            "duration_ms": 0, "phase": "total",
            "status": "error", "error_text": "barrier broken",
            "provider": provider, "key_name": None,
        })
        return

    t0 = time.time()
    lease: Optional[Lease] = None
    status = "success"
    error_text = None
    key_name = None

    try:
        # 1) acquire lease
        lease = acquire_lease(
            cfg, provider=provider, run_id=run_id,
            user_id=user_id, session_id=session_id, school_id=school_id,
            wait=True, max_wait_sec=lease_wait_sec, lease_ttl_sec=lease_ttl_sec,
        )
        key_name = lease.key_name

        # 2) API call (mock or real)
        if mock_mode:
            time.sleep(random.uniform(mock_latency[0] / 1000, mock_latency[1] / 1000))
        else:
            # Real mode: 실제 provider API 호출
            _call_real_api(provider, lease.key_payload)

        # 3) release
        release_lease(cfg, lease.lease_id, state="released")

    except TimeoutError:
        status = "timeout"
        error_text = "lease acquire timeout"
    except Exception as e:
        status = "error"
        error_text = f"{type(e).__name__}: {e}"
        if lease:
            try:
                release_lease(cfg, lease.lease_id, state="error")
            except Exception:
                pass

    duration_ms = int((time.time() - t0) * 1000)
    results_queue.put({
        "test_id": test_id, "worker_id": worker_id, "request_seq": 1,
        "started_at": _now_iso(), "finished_at": _now_iso(),
        "duration_ms": duration_ms, "phase": "total",
        "status": status, "error_text": error_text,
        "provider": provider, "key_name": key_name,
    })


def _call_real_api(provider: str, key_payload: dict):
    """Provider별 경량 테스트 요청."""
    if provider == "google_imagen":
        from providers.google_imagen import generate_images
        generate_images(
            api_key=key_payload["api_key"],
            prompt="A simple red circle on white background",
            num_images=1,
        )
    elif provider == "openai":
        import requests as req
        resp = req.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {key_payload['api_key']}",
                     "Content-Type": "application/json"},
            json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "ping"}],
                  "max_tokens": 5},
            timeout=30,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"OpenAI API {resp.status_code}: {resp.text[:200]}")
    elif provider == "elevenlabs":
        from providers.elevenlabs import text_to_speech
        text_to_speech(
            api_key=key_payload["api_key"],
            voice_id="21m00Tcm4TlvDq8ikWAM",  # Rachel (default)
            text="Hello stress test",
        )
    else:
        # 알 수 없는 provider: mock 처리
        time.sleep(random.uniform(0.1, 0.5))


# ── 결과 저장 ────────────────────────────────────────────

def _flush_samples(cfg: AppConfig, results_queue: queue.Queue):
    batch: list[dict] = []
    while True:
        try:
            batch.append(results_queue.get_nowait())
        except queue.Empty:
            break
    if not batch:
        return
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        for s in batch:
            cur.execute("""
                INSERT INTO stress_test_samples (
                    test_id, worker_id, request_seq, started_at, finished_at,
                    duration_ms, phase, status, error_text, provider, key_name
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                s["test_id"], s["worker_id"], s["request_seq"],
                s["started_at"], s["finished_at"], s["duration_ms"],
                s["phase"], s["status"], s.get("error_text"),
                s.get("provider"), s.get("key_name"),
            ))
        conn.commit()
    finally:
        conn.close()


# ── cleanup ──────────────────────────────────────────────

def _cleanup_stress_artifacts(cfg: AppConfig, test_id: str):
    conn = get_db(cfg)
    try:
        prefix = f"__stress_{test_id[:8]}_%"
        t = _now_iso()
        cur = conn.cursor()
        cur.execute("DELETE FROM api_key_waiters WHERE user_id LIKE ?", (prefix,))
        cur.execute(
            "UPDATE api_key_leases SET state='released', released_at=? "
            "WHERE user_id LIKE ? AND state='active'", (t, prefix),
        )
        cur.execute("DELETE FROM active_jobs WHERE user_id LIKE ?", (prefix,))
        conn.commit()
    finally:
        conn.close()


# ── summary ──────────────────────────────────────────────

def _compute_summary(cfg: AppConfig, test_id: str) -> dict:
    conn = get_db(cfg)
    try:
        cur = conn.cursor()

        cur.execute("SELECT COUNT(*) AS total FROM stress_test_samples WHERE test_id=?", (test_id,))
        total = int(cur.fetchone()["total"])

        cur.execute("SELECT COUNT(*) AS c FROM stress_test_samples WHERE test_id=? AND status='success'", (test_id,))
        successes = int(cur.fetchone()["c"])

        cur.execute("SELECT COUNT(*) AS c FROM stress_test_samples WHERE test_id=? AND status='timeout'", (test_id,))
        timeouts = int(cur.fetchone()["c"])

        cur.execute("SELECT COUNT(*) AS c FROM stress_test_samples WHERE test_id=? AND status='error'", (test_id,))
        errors = int(cur.fetchone()["c"])

        cur.execute(
            "SELECT duration_ms FROM stress_test_samples "
            "WHERE test_id=? AND status='success' ORDER BY duration_ms", (test_id,),
        )
        durations = [row["duration_ms"] for row in cur.fetchall()]

        avg_ms = int(sum(durations) / len(durations)) if durations else 0
        p50 = durations[len(durations) // 2] if durations else 0
        p95 = durations[int(len(durations) * 0.95)] if durations else 0
        p99 = durations[int(len(durations) * 0.99)] if durations else 0

        cur.execute(
            "SELECT key_name, COUNT(*) AS c FROM stress_test_samples "
            "WHERE test_id=? AND key_name IS NOT NULL GROUP BY key_name", (test_id,),
        )
        key_dist = {row["key_name"]: row["c"] for row in cur.fetchall()}

        return {
            "total_requests": total, "successes": successes,
            "timeouts": timeouts, "errors": errors,
            "failures": total - successes,
            "success_rate": round(successes / total * 100, 1) if total else 0,
            "avg_latency_ms": avg_ms, "p50_ms": p50, "p95_ms": p95, "p99_ms": p99,
            "max_latency_ms": durations[-1] if durations else 0,
            "key_distribution": key_dist,
        }
    finally:
        conn.close()


# ── 단일 라운드 실행 (burst) ──────────────────────────────

def _run_single_round(
    cfg: AppConfig,
    test_id: str,
    plan_id: str,
    provider: str,
    num_users: int,
    plan_config: StressPlanConfig,
    admin_user_id: str,
    progress: dict,
    stop_event: threading.Event,
) -> dict:
    """N명 동시 burst 1라운드 실행. Returns summary dict."""
    round_label = f"{provider}_{num_users}"

    # DB에 라운드 기록
    conn = get_db(cfg)
    try:
        t = _now_iso()
        config_snapshot = {
            "num_users": num_users, "provider": provider,
            "mock_mode": plan_config.mock_mode,
            "mock_latency_min_ms": plan_config.mock_latency_min_ms,
            "mock_latency_max_ms": plan_config.mock_latency_max_ms,
        }
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO stress_test_runs
                (test_id, created_at, admin_user_id, status, config_json,
                 started_at, plan_id, round_label)
            VALUES (?, ?, ?, 'running', ?, ?, ?, ?)
        """, (test_id, t, admin_user_id, json.dumps(config_snapshot), t, plan_id, round_label))
        conn.commit()
    finally:
        conn.close()

    # concurrency 제한 해제
    originals = _boost_concurrency(cfg, provider, max(num_users + 10, 100))

    try:
        results_q: queue.Queue = queue.Queue()
        barrier = threading.Barrier(num_users, timeout=30)
        threads = []

        for wid in range(num_users):
            t = threading.Thread(
                target=_burst_worker,
                args=(cfg, test_id, wid, provider,
                      "default", plan_config.mock_mode,
                      (plan_config.mock_latency_min_ms, plan_config.mock_latency_max_ms),
                      plan_config.lease_wait_sec, plan_config.lease_ttl_sec,
                      barrier, results_q),
                daemon=True,
            )
            threads.append(t)
            t.start()

        # 완료 대기
        for th in threads:
            th.join(timeout=plan_config.lease_wait_sec + 10)

        _flush_samples(cfg, results_q)
        _cleanup_stress_artifacts(cfg, test_id)
    finally:
        # concurrency 복원
        _restore_concurrency(cfg, originals)

    summary = _compute_summary(cfg, test_id)

    # 라운드 완료 기록
    final_status = "cancelled" if stop_event.is_set() else "completed"
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        cur.execute("""
            UPDATE stress_test_runs SET status=?, finished_at=?, summary_json=?
            WHERE test_id=?
        """, (final_status, _now_iso(), json.dumps(summary, ensure_ascii=False), test_id))
        conn.commit()
    finally:
        conn.close()

    return summary


# ── Plan 오케스트레이터 ──────────────────────────────────

def run_stress_plan(
    cfg: AppConfig,
    plan_id: str,
    plan_config: StressPlanConfig,
    admin_user_id: str,
    progress: dict,
    stop_event: threading.Event,
):
    """Plan: provider × user_counts 조합을 순차 실행.

    progress dict로 UI에 실시간 상태 전달.
    """
    rounds = []
    for provider in plan_config.providers:
        for count in sorted(plan_config.user_counts):
            rounds.append((provider, count))

    progress.update({
        "plan_id": plan_id,
        "status": "running",
        "total_rounds": len(rounds),
        "completed_rounds": 0,
        "current_round": "",
        "round_results": [],
    })

    for i, (provider, num_users) in enumerate(rounds):
        if stop_event.is_set():
            break

        round_label = f"{provider} × {num_users}명"
        progress["current_round"] = round_label
        progress["current_provider"] = provider
        progress["current_users"] = num_users

        test_id = str(uuid.uuid4())

        logger.info("Plan %s: round %d/%d — %s", plan_id, i + 1, len(rounds), round_label)

        summary = _run_single_round(
            cfg, test_id, plan_id, provider, num_users,
            plan_config, admin_user_id, progress, stop_event,
        )

        progress["round_results"].append({
            "test_id": test_id,
            "provider": provider,
            "num_users": num_users,
            "round_label": round_label,
            **summary,
        })
        progress["completed_rounds"] = i + 1

        # 라운드 간 1초 휴식 (DB 안정화)
        if not stop_event.is_set() and i < len(rounds) - 1:
            time.sleep(1)

    progress["status"] = "cancelled" if stop_event.is_set() else "completed"
    logger.info("Plan %s finished: %s", plan_id, progress["status"])


# ── DB query helpers ─────────────────────────────────────

def _to_dicts(rows) -> list[dict]:
    return [dict(r) for r in (rows or [])]


def list_stress_test_runs(cfg: AppConfig, limit: int = 50, plan_id: str | None = None) -> list[dict]:
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        if plan_id:
            cur.execute(
                "SELECT * FROM stress_test_runs WHERE plan_id=? ORDER BY created_at ASC LIMIT ?",
                (plan_id, limit),
            )
        else:
            cur.execute(
                "SELECT * FROM stress_test_runs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
        return _to_dicts(cur.fetchall())
    finally:
        conn.close()


def list_plan_ids(cfg: AppConfig, limit: int = 20) -> list[dict]:
    """plan_id별 최신 기록 조회."""
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT plan_id, MIN(created_at) AS started_at,
                   COUNT(*) AS round_count,
                   GROUP_CONCAT(DISTINCT round_label) AS rounds
            FROM stress_test_runs
            WHERE plan_id IS NOT NULL
            GROUP BY plan_id
            ORDER BY started_at DESC
            LIMIT ?
        """, (limit,))
        return _to_dicts(cur.fetchall())
    finally:
        conn.close()


def get_stress_test_run(cfg: AppConfig, test_id: str) -> dict | None:
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM stress_test_runs WHERE test_id=?", (test_id,))
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_stress_test_samples(cfg: AppConfig, test_id: str) -> list[dict]:
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM stress_test_samples WHERE test_id=? ORDER BY started_at",
            (test_id,),
        )
        return _to_dicts(cur.fetchall())
    finally:
        conn.close()


def delete_stress_plan(cfg: AppConfig, plan_id: str):
    """Plan에 속한 모든 라운드 + 샘플 삭제."""
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        cur.execute("SELECT test_id FROM stress_test_runs WHERE plan_id=?", (plan_id,))
        test_ids = [row["test_id"] for row in cur.fetchall()]
        for tid in test_ids:
            cur.execute("DELETE FROM stress_test_samples WHERE test_id=?", (tid,))
        cur.execute("DELETE FROM stress_test_runs WHERE plan_id=?", (plan_id,))
        conn.commit()
    finally:
        conn.close()


def delete_stress_test_run(cfg: AppConfig, test_id: str):
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM stress_test_samples WHERE test_id=?", (test_id,))
        cur.execute("DELETE FROM stress_test_runs WHERE test_id=?", (test_id,))
        conn.commit()
    finally:
        conn.close()


def list_stress_rounds_by_provider(cfg: AppConfig, provider: str) -> list[dict]:
    """특정 provider의 모든 완료된 라운드를 조회 (최신순).

    json_extract 대신 Python 측 필터링 (libSQL 호환성).
    """
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM stress_test_runs "
            "WHERE status='completed' "
            "ORDER BY created_at DESC LIMIT 500",
        )
        rows = _to_dicts(cur.fetchall())
        result = []
        for r in rows:
            try:
                cfg_json = json.loads(r.get("config_json", "{}") or "{}")
            except Exception:
                cfg_json = {}
            if cfg_json.get("provider") == provider:
                result.append(r)
        return result[:200]
    finally:
        conn.close()


def list_tested_providers(cfg: AppConfig) -> list[str]:
    """테스트 데이터가 있는 provider 목록.

    json_extract 대신 Python 측 파싱 (libSQL 호환성).
    """
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT config_json FROM stress_test_runs
            WHERE config_json IS NOT NULL AND status='completed'
        """)
        providers = set()
        for row in cur.fetchall():
            try:
                cfg_json = json.loads(row["config_json"] or "{}")
            except Exception:
                continue
            p = cfg_json.get("provider")
            if p:
                providers.add(p)
        return sorted(providers)
    finally:
        conn.close()


def get_provider_key_info(cfg: AppConfig) -> list[dict]:
    """provider별 활성 키 개수 및 총 동시수용 한도 조회."""
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT provider,
                   COUNT(*) AS key_count,
                   SUM(concurrency_limit) AS total_concurrency
            FROM api_keys
            WHERE is_active = 1
            GROUP BY provider
            ORDER BY provider
        """)
        return _to_dicts(cur.fetchall())
    finally:
        conn.close()
