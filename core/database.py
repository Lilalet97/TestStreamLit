# core/database.py
"""공유 DB 연결 모듈 — db.py, key_pool.py 양쪽에서 import.

- DictCursor / DictConn: libsql 결과를 dict로 반환하는 래퍼
- get_db(): 커넥션 캐싱 + 헬스체크 + 주기적 sync
- throttled_sync() / force_sync(): Turso 동기화 제어
"""
import sqlite3
import time
from core.config import AppConfig

try:
    import libsql_experimental as libsql
    _HAS_LIBSQL = True
except ImportError:
    _HAS_LIBSQL = False


# ── libsql 호환 dict-row wrapper ──────────────────────────

class DictCursor:
    """DB-API cursor를 감싸서 fetchone/fetchall이 dict를 반환."""
    __slots__ = ("_cur",)

    def __init__(self, cur):
        self._cur = cur

    def execute(self, *a, **kw):
        if len(a) >= 2 and isinstance(a[1], list):
            a = (a[0], tuple(a[1]), *a[2:])
        self._cur.execute(*a, **kw)
        return self

    def fetchone(self):
        row = self._cur.fetchone()
        if row is None or not self._cur.description:
            return row
        return dict(zip([d[0] for d in self._cur.description], row))

    def fetchall(self):
        rows = self._cur.fetchall()
        if not rows or not self._cur.description:
            return rows
        cols = [d[0] for d in self._cur.description]
        return [dict(zip(cols, r)) for r in rows]

    @property
    def description(self):
        return self._cur.description

    @property
    def lastrowid(self):
        return self._cur.lastrowid


class DictConn:
    """libsql connection을 감싸서 cursor가 dict row를 반환하도록 함."""
    __slots__ = ("_conn",)

    def __init__(self, conn):
        self._conn = conn

    def cursor(self):
        return DictCursor(self._conn.cursor())

    def execute(self, *a, **kw):
        if len(a) >= 2 and isinstance(a[1], list):
            a = (a[0], tuple(a[1]), *a[2:])
        return DictCursor(self._conn.execute(*a, **kw))

    def commit(self):
        self._conn.commit()
        throttled_sync()

    def sync(self):
        """Force immediate sync to Turso."""
        force_sync()

    def close(self):
        pass  # cached → 닫지 않음


# ── 싱글 커넥션 캐시 ──────────────────────────────────────

_cached_conn = None
_is_turso: bool = False
_last_sync_ts: float = 0.0
_last_write_sync_ts: float = 0.0
_SYNC_INTERVAL = 30           # 주기적 sync: 30초 (Streamlit Cloud 안정성)
_WRITE_SYNC_INTERVAL = 5      # 쓰기 후 sync 쓰로틀: 5초


def throttled_sync():
    """쓰기 후 Turso 동기화 (쓰로틀: _WRITE_SYNC_INTERVAL 초마다 최대 1회)."""
    global _last_write_sync_ts, _last_sync_ts
    if not _is_turso or _cached_conn is None:
        return
    now = time.time()
    if (now - _last_write_sync_ts) < _WRITE_SYNC_INTERVAL:
        return
    try:
        _cached_conn.sync()
        _last_write_sync_ts = now
        _last_sync_ts = now
    except Exception:
        pass


def force_sync():
    """즉시 Turso 동기화 (쓰로틀 무시). init_db 등 중요 시점에 호출."""
    global _last_write_sync_ts, _last_sync_ts
    if not _is_turso or _cached_conn is None:
        return
    try:
        _cached_conn.sync()
        now = time.time()
        _last_write_sync_ts = now
        _last_sync_ts = now
    except Exception:
        pass


def get_db(cfg: AppConfig):
    """캐시된 libsql/sqlite3 커넥션 반환.

    - 헬스체크(SELECT 1): 연결 끊김 시 자동 재생성
    - 주기적 sync: Turso 원격 DB인 경우 _SYNC_INTERVAL마다
    """
    global _cached_conn, _last_sync_ts, _is_turso

    now = time.time()

    # 1) 캐시 히트 → 헬스체크 + 주기적 sync
    if _HAS_LIBSQL and _cached_conn is not None:
        try:
            _cached_conn.execute("SELECT 1")
        except Exception:
            _cached_conn = None  # 재연결 유도

        if _cached_conn is not None:
            if cfg.turso_database_url and (now - _last_sync_ts) > _SYNC_INTERVAL:
                try:
                    _cached_conn.sync()
                    _last_sync_ts = now
                except Exception:
                    pass
            return DictConn(_cached_conn)

    # 2) 신규 연결
    url = cfg.turso_database_url
    token = cfg.turso_auth_token

    if url and _HAS_LIBSQL:
        raw = libsql.connect(cfg.runs_db_path, sync_url=url, auth_token=token)
        try:
            raw.sync()
            _last_sync_ts = now
            _last_write_sync_ts = now
        except Exception:
            pass
        _cached_conn = raw
        _is_turso = True
        conn = DictConn(raw)
    elif _HAS_LIBSQL:
        raw = libsql.connect(cfg.runs_db_path)
        _cached_conn = raw
        _is_turso = False
        conn = DictConn(raw)
    else:
        # libsql 미설치 시 (Windows 개발환경 등) 기존 sqlite3 fallback
        conn = sqlite3.connect(cfg.runs_db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute("PRAGMA busy_timeout=5000;")
        except Exception:
            pass
        return conn

    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
    except Exception:
        pass
    return conn
