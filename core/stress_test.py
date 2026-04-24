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
from datetime import datetime, timezone
from typing import List

from core.config import AppConfig
from core.database import get_db
from core.key_pool import acquire_lease, release_lease

_log = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# Provider 정렬 순서: text→text, text→image, text→video, text→sound
PROVIDER_ORDER = ["openai", "midjourney", "google_imagen", "kling", "google_veo", "grok", "elevenlabs", "suno"]


# ── Plan config ──────────────────────────────────────────

@dataclass
class StressPlanConfig:
    """부하 테스트 플랜 설정.

    test_mode:
      - "mock": 알고리즘 검증 (DB 접근 없이 capacity 시뮬레이션)
      - "burst": 키 부하 테스트 (FIFO 우회, 동시 burst API 호출)
      - "realistic": 실제 부하 테스트 (FIFO 통해 burst_window_sec 내 랜덤 순차 요청)
    """
    providers: List[str] = field(default_factory=lambda: ["openai"])
    user_counts: List[int] = field(default_factory=lambda: [5, 10, 15])  # max 200
    test_mode: str = "mock"  # "mock" | "burst" | "realistic"
    mock_mode: bool = True   # backward compat: mock_mode=True ↔ test_mode="mock"
    mock_latency_min_ms: int = 100
    mock_latency_max_ms: int = 500
    lease_wait_sec: int = 30
    lease_ttl_sec: int = 60
    burst_window_sec: int = 60

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, s: str) -> "StressPlanConfig":
        return cls(**json.loads(s))


# ── 키 풀 concurrency 임시 변경 ──────────────────────────

def _boost_limits(cfg: AppConfig, provider: str, new_concurrency: int) -> list[tuple]:
    """해당 provider의 키 concurrency_limit와 rpm_limit를 일시적으로 올린다.
    Returns: [(api_key_id, original_concurrency, original_rpm), ...]
    """
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT api_key_id, concurrency_limit, rpm_limit FROM api_keys WHERE provider=?",
            (provider,),
        )
        originals = [(row["api_key_id"], row["concurrency_limit"], row["rpm_limit"]) for row in cur.fetchall()]
        cur.execute(
            "UPDATE api_keys SET concurrency_limit=?, rpm_limit=? WHERE provider=?",
            (new_concurrency, max(new_concurrency * 10, 9999), provider),
        )
        conn.commit()
        return originals
    finally:
        conn.close()


def _restore_limits(cfg: AppConfig, originals: list[tuple]):
    """원래 concurrency_limit, rpm_limit로 복원 + 테스트로 쌓인 RPM 카운터 정리."""
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        for key_id, conc, rpm in originals:
            cur.execute(
                "UPDATE api_keys SET concurrency_limit=?, rpm_limit=? WHERE api_key_id=?",
                (conc, rpm, key_id),
            )
            cur.execute(
                "DELETE FROM api_key_usage_minute WHERE api_key_id=?",
                (key_id,),
            )
        conn.commit()
    finally:
        conn.close()


# ── Burst worker ─────────────────────────────────────────

def _get_active_keys(cfg: AppConfig, provider: str) -> list[dict]:
    """해당 provider의 활성 키 목록 조회 (Mock용 — payload 없음)."""
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT api_key_id, key_name, concurrency_limit, rpm_limit "
            "FROM api_keys WHERE provider=? AND is_active=1 ORDER BY priority DESC",
            (provider,),
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def _get_active_keys_full(cfg: AppConfig, provider: str) -> list[dict]:
    """해당 provider의 활성 키 목록 조회 (Real용 — payload 포함)."""
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT api_key_id, key_name, key_payload, concurrency_limit, rpm_limit "
            "FROM api_keys WHERE provider=? AND is_active=1 ORDER BY priority DESC",
            (provider,),
        )
        rows = []
        for r in cur.fetchall():
            d = dict(r)
            try:
                d["key_payload"] = json.loads(d["key_payload"]) if isinstance(d["key_payload"], str) else d["key_payload"]
            except Exception:
                d["key_payload"] = {}
            rows.append(d)
        return rows
    finally:
        conn.close()


def _real_burst_worker(
    cfg: AppConfig,
    test_id: str,
    worker_id: int,
    provider: str,
    key_name: str,
    key_payload: dict,
    barrier: threading.Barrier,
    results_queue: queue.Queue,
):
    """Real 모드: FIFO 우회, 직접 키를 할당받아 실제 API 호출."""
    try:
        barrier.wait(timeout=30)
    except threading.BrokenBarrierError:
        t_err = _now_iso()
        results_queue.put({
            "test_id": test_id, "worker_id": worker_id, "request_seq": 1,
            "started_at": t_err, "finished_at": t_err,
            "duration_ms": 0, "phase": "total",
            "status": "error", "error_text": "barrier broken",
            "provider": provider, "key_name": key_name,
        })
        return

    started_at = _now_iso()
    t0 = time.time()
    status = "success"
    error_text = None

    try:
        _call_real_api(cfg, provider, key_payload)
    except Exception as e:
        status = "error"
        error_text = f"{type(e).__name__}: {e}"

    duration_ms = int((time.time() - t0) * 1000)
    results_queue.put({
        "test_id": test_id, "worker_id": worker_id, "request_seq": 1,
        "started_at": started_at, "finished_at": _now_iso(),
        "duration_ms": duration_ms, "phase": "total",
        "status": status, "error_text": error_text,
        "provider": provider, "key_name": key_name,
    })


def _mock_realistic_worker(
    cfg: AppConfig,
    test_id: str,
    worker_id: int,
    provider: str,
    school_id: str,
    delay_sec: float,
    lease_wait_sec: int,
    lease_ttl_sec: int,
    mock_latency: tuple[int, int],
    results_queue: queue.Queue,
):
    """Mock + FIFO 워커: acquire_lease → mock sleep (API 대신) → release.

    알고리즘 검증용: FIFO 대기열·키 배정 로직을 실제로 실행하되,
    API 호출 없이 mock latency로 대체.
    """
    time.sleep(delay_sec)

    user_id = f"__stress_{test_id[:8]}_{worker_id}"
    session_id = f"stress_sess_{worker_id}"
    run_id = str(uuid.uuid4())

    started_at = _now_iso()
    t0 = time.time()
    lease = None
    status = "success"
    error_text = None
    key_name = None

    try:
        lease = acquire_lease(
            cfg, provider=provider, run_id=run_id,
            user_id=user_id, session_id=session_id, school_id=school_id,
            wait=True, max_wait_sec=lease_wait_sec, lease_ttl_sec=lease_ttl_sec,
        )
        key_name = lease.key_name
        # API 호출 대신 mock sleep
        time.sleep(random.uniform(mock_latency[0] / 1000, mock_latency[1] / 1000))
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
        "started_at": started_at, "finished_at": _now_iso(),
        "duration_ms": duration_ms, "phase": "total",
        "status": status, "error_text": error_text,
        "provider": provider, "key_name": key_name,
    })


def _realistic_worker(
    cfg: AppConfig,
    test_id: str,
    worker_id: int,
    provider: str,
    school_id: str,
    delay_sec: float,
    lease_wait_sec: int,
    lease_ttl_sec: int,
    results_queue: queue.Queue,
):
    """Realistic 모드: 지정된 delay 후 FIFO acquire → 실제 API 호출 → release.

    실제 운영 환경처럼 학생들이 산발적으로 요청하는 패턴을 시뮬레이션.
    """
    # 랜덤 딜레이 (60초 내 분산)
    time.sleep(delay_sec)

    user_id = f"__stress_{test_id[:8]}_{worker_id}"
    session_id = f"stress_sess_{worker_id}"
    run_id = str(uuid.uuid4())

    started_at = _now_iso()
    t0 = time.time()
    lease = None
    status = "success"
    error_text = None
    key_name = None

    try:
        lease = acquire_lease(
            cfg, provider=provider, run_id=run_id,
            user_id=user_id, session_id=session_id, school_id=school_id,
            wait=True, max_wait_sec=lease_wait_sec, lease_ttl_sec=lease_ttl_sec,
        )
        key_name = lease.key_name
        _call_real_api(cfg, provider, lease.key_payload)
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
        "started_at": started_at, "finished_at": _now_iso(),
        "duration_ms": duration_ms, "phase": "total",
        "status": status, "error_text": error_text,
        "provider": provider, "key_name": key_name,
    })


def _run_mock_workers(
    cfg: AppConfig,
    test_id: str,
    provider: str,
    num_users: int,
    plan_config: StressPlanConfig,
    results_q: queue.Queue,
) -> list[threading.Thread]:
    """Mock 워커: FIFO acquire → mock sleep → release (burst_window 내 분산).

    실제 운영과 동일한 FIFO 대기열·키 배정 로직을 검증하되,
    API 호출 없이 mock latency로 대체.
    """
    school_id = "stress_test"
    window = plan_config.burst_window_sec
    threads = []
    for i in range(num_users):
        delay = random.uniform(0, window)
        t = threading.Thread(
            target=_mock_realistic_worker,
            args=(
                cfg, test_id, i, provider, school_id, delay,
                plan_config.lease_wait_sec, plan_config.lease_ttl_sec,
                (plan_config.mock_latency_min_ms, plan_config.mock_latency_max_ms),
                results_q,
            ),
            daemon=True,
        )
        t.start()
        threads.append(t)
    return threads


def _run_burst_workers(
    cfg: AppConfig,
    test_id: str,
    provider: str,
    num_users: int,
    plan_config: StressPlanConfig,
    results_q: queue.Queue,
) -> list[threading.Thread]:
    """Burst 워커: FIFO 우회, 직접 키 배분 후 동시 API 호출."""
    keys = _get_active_keys_full(cfg, provider)
    if not keys:
        raise RuntimeError(f"provider '{provider}'에 활성 키가 없습니다")

    # capacity 한도 일시 상향
    originals = _boost_limits(cfg, provider, num_users)

    barrier = threading.Barrier(num_users, timeout=30)
    threads = []
    for i in range(num_users):
        assigned = keys[i % len(keys)]
        t = threading.Thread(
            target=_real_burst_worker,
            args=(
                cfg, test_id, i, provider,
                assigned["key_name"], assigned["key_payload"],
                barrier, results_q,
            ),
            daemon=True,
        )
        t.start()
        threads.append(t)

    return threads, originals


def _run_realistic_workers(
    cfg: AppConfig,
    test_id: str,
    provider: str,
    num_users: int,
    plan_config: StressPlanConfig,
    results_q: queue.Queue,
) -> list[threading.Thread]:
    """Realistic 워커: burst_window 내 랜덤 분산 요청 (FIFO 사용)."""
    school_id = "stress_test"
    window = plan_config.burst_window_sec
    threads = []
    for i in range(num_users):
        delay = random.uniform(0, window)
        t = threading.Thread(
            target=_realistic_worker,
            args=(
                cfg, test_id, i, provider, school_id, delay,
                plan_config.lease_wait_sec, plan_config.lease_ttl_sec,
                results_q,
            ),
            daemon=True,
        )
        t.start()
        threads.append(t)
    return threads


def _call_real_api(cfg: AppConfig, provider: str, key_payload: dict):
    """Provider별 경량 테스트 요청."""
    if not key_payload:
        raise ValueError("key_payload가 비어있습니다")
    if provider == "google_imagen":
        from providers.google_imagen import gemini_generate
        gemini_generate(
            api_key=key_payload["api_key"],
            parts=[{"text": "A simple red circle on white background"}],
            model=cfg.google_imagen_model,
            num_images=1,
        )
    elif provider == "openai":
        import requests as req
        resp = req.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {key_payload['api_key']}",
                     "Content-Type": "application/json"},
            json={"model": cfg.openai_model, "messages": [{"role": "user", "content": "ping"}],
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
            model_id=cfg.elevenlabs_model,
        )
    elif provider == "kling":
        from providers.kling import submit_image
        ak = key_payload.get("access_key", "")
        sk = key_payload.get("secret_key", "")
        if not ak or not sk:
            raise ValueError("kling key_payload에 access_key/secret_key가 없습니다")
        submit_image(
            access_key=ak,
            secret_key=sk,
            endpoint="https://api.klingai.com/v1/images/generations",
            payload={"model_name": cfg.kling_model, "prompt": "A simple red circle",
                     "image_num": 1, "aspect_ratio": "1:1"},
        )
    elif provider == "midjourney":
        # useapi.net Midjourney: 키풀에서 api_key(토큰)과 channel을 받아 경량 테스트
        _token = key_payload.get("api_key", "")
        _channel = key_payload.get("channel", "")
        if not _token:
            raise ValueError("midjourney key_payload에 api_key가 없습니다")
        from providers.useapi_mj import imagine
        imagine(
            api_token=_token,
            prompt="A simple red circle on white background --fast",
            channel=_channel,
            timeout=120,
        )
    else:
        # 미지원 provider (google_veo, grok 등 동영상): mock 처리
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
        n = len(durations)
        p50 = durations[min((n - 1) // 2, n - 1)] if durations else 0
        p95 = durations[min(int((n - 1) * 0.95), n - 1)] if durations else 0
        p99 = durations[min(int((n - 1) * 0.99), n - 1)] if durations else 0

        cur.execute(
            "SELECT key_name, COUNT(*) AS c FROM stress_test_samples "
            "WHERE test_id=? AND key_name IS NOT NULL GROUP BY key_name", (test_id,),
        )
        key_dist = {row["key_name"]: row["c"] for row in cur.fetchall()}

        # 키별 상세 메트릭
        cur.execute("""
            SELECT key_name,
                   COUNT(*) AS total,
                   SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) AS ok,
                   SUM(CASE WHEN status='timeout' THEN 1 ELSE 0 END) AS tm,
                   SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) AS er,
                   AVG(CASE WHEN status='success' THEN duration_ms END) AS avg_ms
            FROM stress_test_samples
            WHERE test_id=? AND key_name IS NOT NULL
            GROUP BY key_name
        """, (test_id,))
        key_details = {}
        for row in cur.fetchall():
            kn = row["key_name"]
            kt = int(row["total"])
            ko = int(row["ok"])
            key_details[kn] = {
                "requests": kt,
                "successes": ko,
                "timeouts": int(row["tm"]),
                "errors": int(row["er"]),
                "success_rate": round(ko / kt * 100, 1) if kt else 0,
                "avg_latency_ms": int(row["avg_ms"] or 0),
            }

        return {
            "total_requests": total, "successes": successes,
            "timeouts": timeouts, "errors": errors,
            "failures": total - successes,
            "success_rate": round(successes / total * 100, 1) if total else 0,
            "avg_latency_ms": avg_ms, "p50_ms": p50, "p95_ms": p95, "p99_ms": p99,
            "max_latency_ms": durations[-1] if durations else 0,
            "key_distribution": key_dist,
            "key_details": key_details,
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
    """N명 라운드 실행 (mock/burst/realistic). Returns summary dict."""
    num_users = max(1, min(num_users, 200))
    mode = plan_config.test_mode
    round_label = f"{provider}_{num_users}"

    # DB에 라운드 기록
    conn = get_db(cfg)
    try:
        t = _now_iso()
        config_snapshot = {
            "num_users": num_users, "provider": provider,
            "test_mode": mode,
            "mock_mode": mode == "mock",  # backward compat for viewer report
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

    results_q: queue.Queue = queue.Queue()
    threads = []

    join_timeout = plan_config.burst_window_sec + plan_config.lease_wait_sec + 30

    if mode == "mock":
        # Mock: FIFO 경유 + mock sleep (알고리즘 검증)
        threads = _run_mock_workers(cfg, test_id, provider, num_users, plan_config, results_q)
        for th in threads:
            th.join(timeout=join_timeout)
        _flush_samples(cfg, results_q)
        _cleanup_stress_artifacts(cfg, test_id)

    elif mode == "burst":
        # Burst: FIFO 우회, capacity 기반 배분 후 동시 API 호출
        threads, boost_originals = _run_burst_workers(cfg, test_id, provider, num_users, plan_config, results_q)
        for th in threads:
            th.join(timeout=plan_config.lease_wait_sec + 30)
        _flush_samples(cfg, results_q)
        _restore_limits(cfg, boost_originals)

    elif mode == "realistic":
        # Realistic: FIFO 통해 burst_window 내 랜덤 순차 요청
        threads = _run_realistic_workers(cfg, test_id, provider, num_users, plan_config, results_q)
        for th in threads:
            th.join(timeout=join_timeout)
        _flush_samples(cfg, results_q)
        _cleanup_stress_artifacts(cfg, test_id)

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

        _log.info("Plan %s: round %d/%d — %s", plan_id, i + 1, len(rounds), round_label)

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

        # 라운드 간 휴식: burst=60초 (RPM 리셋), mock/realistic=5초 (cleanup 여유)
        if not stop_event.is_set() and i < len(rounds) - 1:
            wait_sec = 5 if plan_config.test_mode == "mock" else 60
            progress["current_round"] = f"다음 라운드 대기 ({wait_sec}s)..."
            for _ in range(wait_sec):
                if stop_event.is_set():
                    break
                time.sleep(1)

    progress["status"] = "cancelled" if stop_event.is_set() else "completed"
    _log.info("Plan %s finished: %s", plan_id, progress["status"])


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


def list_plan_ids(cfg: AppConfig, limit: int = 20, mock_mode: bool | None = None,
                   test_mode: str | None = None) -> list[dict]:
    """plan_id별 최신 기록 조회.

    test_mode: "mock" | "burst" | "realistic" 필터 (우선).
    mock_mode: True=Mock만, False=Real만 (하위호환).
    """
    conn = get_db(cfg)
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT plan_id, MIN(created_at) AS started_at,
                   COUNT(*) AS round_count,
                   GROUP_CONCAT(DISTINCT round_label) AS rounds,
                   MIN(config_json) AS first_config_json
            FROM stress_test_runs
            WHERE plan_id IS NOT NULL
            GROUP BY plan_id
            ORDER BY started_at DESC
            LIMIT ?
        """, (limit * 5,))  # 필터링 전 여유분 조회
        rows = _to_dicts(cur.fetchall())

        result = []
        for r in rows:
            cj = {}
            try:
                cj = json.loads(r.get("first_config_json", "{}") or "{}")
            except Exception:
                pass
            r_test_mode = cj.get("test_mode", "mock" if cj.get("mock_mode", True) else "burst")
            r["test_mode"] = r_test_mode
            r["mock_mode"] = cj.get("mock_mode", True)
            r.pop("first_config_json", None)

            # test_mode 필터 (우선)
            if test_mode is not None:
                if r_test_mode != test_mode:
                    continue
            elif mock_mode is not None:
                is_mock = r["mock_mode"]
                if is_mock != mock_mode:
                    continue

            result.append(r)
            if len(result) >= limit:
                break

        return result
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

    json_extract 대신 Python 측 필터링.
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

    json_extract 대신 Python 측 파싱.
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
        # registry 탭 순서 기준 정렬
        ordered = [p for p in PROVIDER_ORDER if p in providers]
        ordered += sorted(providers - set(ordered))
        return ordered
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
