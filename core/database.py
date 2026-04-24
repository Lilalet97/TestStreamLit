# core/database.py
"""공유 DB 연결 모듈 — db.py, key_pool.py 양쪽에서 import.

- DictCursor / DictConn: sqlite3 결과를 dict로 반환하는 래퍼
- get_db(): 커넥션 캐싱 + 헬스체크
"""
import logging
import sqlite3
from core.config import AppConfig

_log = logging.getLogger(__name__)


# ── dict-row wrapper ──────────────────────────────────────

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

    @property
    def rowcount(self):
        return self._cur.rowcount


class DictConn:
    """sqlite3 connection을 감싸서 cursor가 dict row를 반환하도록 함."""
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

    def rollback(self):
        self._conn.rollback()

    def close(self):
        pass  # cached → 닫지 않음

    @property
    def raw(self):
        return self._conn


# ── 싱글 커넥션 캐시 ──────────────────────────────────────

_cached_conn = None


def _reset_cached_conn():
    """캐시된 연결 강제 초기화 (복구용)."""
    global _cached_conn
    try:
        if _cached_conn is not None:
            _cached_conn.close()
    except Exception:
        pass
    _cached_conn = None


def get_db(cfg: AppConfig):
    """캐시된 sqlite3 커넥션 반환.

    - 헬스체크(SELECT 1): 연결 끊김 시 자동 재생성
    """
    global _cached_conn

    # 캐시 히트 → 헬스체크
    if _cached_conn is not None:
        try:
            _cached_conn.execute("SELECT 1")
        except Exception:
            try:
                _cached_conn.close()
            except Exception:
                pass
            _cached_conn = None

        if _cached_conn is not None:
            return DictConn(_cached_conn)

    # 신규 연결
    conn = sqlite3.connect(cfg.runs_db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
    except Exception as exc:
        _log.warning("PRAGMA 설정 실패: %s", exc)

    _cached_conn = conn
    return DictConn(conn)


def get_db_isolated(cfg: AppConfig):
    """스레드 안전한 개별 연결 반환. 사용 후 반드시 .close() 호출."""
    conn = sqlite3.connect(cfg.runs_db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA busy_timeout=10000;")
    except Exception as exc:
        _log.warning("PRAGMA 설정 실패: %s", exc)

    class _IsolatedConn(DictConn):
        def close(self):
            self._conn.close()

    return _IsolatedConn(conn)
