# ui/stress_report.py
"""부하 테스트 결과 보고서 — Viewer용 Provider별 리포트.

핵심 질문: "우리 교실에서 동시에 몇 명이 사용해도 되는가?"
Viewer가 Provider(기능)를 선택하면 해당 provider의 모든 테스트 데이터를
종합 분석하여 권장 사항을 보여준다.
"""
import json
from dataclasses import dataclass

import pandas as pd
import streamlit as st

from core.config import AppConfig
from core.stress_test import (
    list_tested_providers,
    list_stress_rounds_by_provider,
    get_provider_key_info,
)


# ── 분석 모델 ────────────────────────────────────────────

@dataclass
class RoundResult:
    num_users: int
    total_requests: int
    successes: int
    failures: int
    success_rate: float
    avg_latency_ms: int
    p95_ms: int
    p99_ms: int
    tested_at: str
    is_mock: bool


@dataclass
class ProviderReport:
    provider: str
    rounds: list[RoundResult]       # user_count 오름차순, 최신 결과 우선
    recommended_users: int
    grade: str                      # A / B / C
    comment: str
    key_count: int
    total_concurrency: int


def _parse_rounds(rows: list[dict]) -> list[RoundResult]:
    """DB 행 → RoundResult 리스트. 같은 user_count가 여러번 있으면 최신만."""
    by_users: dict[int, RoundResult] = {}
    for r in rows:
        summary = {}
        try:
            summary = json.loads(r.get("summary_json", "{}") or "{}")
        except Exception:
            pass
        config = {}
        try:
            config = json.loads(r.get("config_json", "{}") or "{}")
        except Exception:
            pass

        num_users = config.get("num_users", 0)
        if not num_users:
            continue

        # 이미 같은 user_count가 있으면 skip (최신순 쿼리이므로 먼저 온 게 최신)
        if num_users in by_users:
            continue

        by_users[num_users] = RoundResult(
            num_users=num_users,
            total_requests=summary.get("total_requests", 0),
            successes=summary.get("successes", 0),
            failures=summary.get("failures", 0),
            success_rate=summary.get("success_rate", 0),
            avg_latency_ms=summary.get("avg_latency_ms", 0),
            p95_ms=summary.get("p95_ms", 0),
            p99_ms=summary.get("p99_ms", 0),
            tested_at=r.get("created_at", "")[:19],
            is_mock=config.get("mock_mode", True),
        )

    result = sorted(by_users.values(), key=lambda x: x.num_users)
    return result


def _compute_recommendation(provider: str, rounds: list[RoundResult]) -> tuple[int, str, str]:
    """라운드 결과로부터 권장 사용자 수, 등급, 코멘트 산출.
    지연 기준은 provider의 결과물 유형에 따라 차등 적용."""
    meta = _get_meta(provider)
    a_ms = meta["grade_a_ms"]
    b_ms = meta["grade_b_ms"]

    recommended = 0
    best_grade = "C"

    for r in rounds:
        if r.success_rate >= 95 and r.avg_latency_ms <= a_ms:
            grade = "A"
        elif r.success_rate >= 80 and r.avg_latency_ms <= b_ms:
            grade = "B"
        else:
            grade = "C"

        if grade in ("A", "B"):
            recommended = r.num_users
            best_grade = grade

    if recommended == 0:
        if rounds:
            worst = rounds[0]
            comment = f"최소 {worst.num_users}명에서도 성공률 {worst.success_rate}% — 안정적인 동시 사용이 어렵습니다"
        else:
            comment = "테스트 데이터가 없습니다"
    else:
        last_ok = next((r for r in reversed(rounds) if r.num_users == recommended), None)
        if best_grade == "A":
            comment = f"{recommended}명까지 안정적 (성공률 {last_ok.success_rate}%, 평균 {last_ok.avg_latency_ms}ms)"
        else:
            comment = f"{recommended}명까지 가능하나 지연 주의 (평균 {last_ok.avg_latency_ms}ms)"

        next_rounds = [r for r in rounds if r.num_users > recommended]
        if next_rounds:
            nr = next_rounds[0]
            comment += f" | {nr.num_users}명은 성공률 {nr.success_rate}%"

    return recommended, best_grade, comment


# ── 스타일 헬퍼 ──────────────────────────────────────────

_GRADE_COLORS = {
    "A": ("#27ae60", "#e8f8f0"),
    "B": ("#f39c12", "#fef9e7"),
    "C": ("#e74c3c", "#fdedec"),
}

# Provider 메타데이터: 라벨, 결과물 유형, 등급 지연 기준(ms)
# grade_a_ms / grade_b_ms: 해당 지연 이하일 때 A/B 등급
_PROVIDER_META = {
    "openai": {
        "label": "GPT (OpenAI)",
        "output": "텍스트",
        "grade_a_ms": 3000,
        "grade_b_ms": 8000,
    },
    "elevenlabs": {
        "label": "ElevenLabs TTS",
        "output": "음성",
        "grade_a_ms": 5000,
        "grade_b_ms": 15000,
    },
    "google_imagen": {
        "label": "NanoBanana (Google Imagen)",
        "output": "이미지",
        "grade_a_ms": 15000,
        "grade_b_ms": 30000,
    },
    "midjourney": {
        "label": "Midjourney",
        "output": "이미지",
        "grade_a_ms": 15000,
        "grade_b_ms": 30000,
    },
    "kling": {
        "label": "Kling Video",
        "output": "동영상",
        "grade_a_ms": 30000,
        "grade_b_ms": 60000,
    },
}

_DEFAULT_META = {"label": None, "output": "기타", "grade_a_ms": 5000, "grade_b_ms": 15000}


def _get_meta(provider: str) -> dict:
    return _PROVIDER_META.get(provider, _DEFAULT_META)


def _provider_label(provider: str) -> str:
    m = _get_meta(provider)
    return m["label"] or provider.upper()


def _provider_output_type(provider: str) -> str:
    return _get_meta(provider)["output"]


def _grade_badge_html(grade: str) -> str:
    fg, _ = _GRADE_COLORS.get(grade, ("#95a5a6", "#f0f0f0"))
    return (
        f'<span style="background:{fg};color:#fff;padding:4px 12px;'
        f'border-radius:8px;font-size:1.1em;font-weight:700;'
        f'letter-spacing:1px;">등급 {grade}</span>'
    )


def _status_icon(provider: str, success_rate: float, avg_latency_ms: int) -> str:
    meta = _get_meta(provider)
    if success_rate >= 95 and avg_latency_ms <= meta["grade_a_ms"]:
        return "✅"
    elif success_rate >= 80 and avg_latency_ms <= meta["grade_b_ms"]:
        return "⚠️"
    return "❌"


def _capacity_html(diff: int) -> str:
    if diff > 0:
        return f'<span style="color:#27ae60;font-weight:600;">+{diff} 여유</span>'
    elif diff == 0:
        return '<span style="color:#f39c12;font-weight:600;">딱 맞음</span>'
    else:
        return f'<span style="color:#e74c3c;font-weight:600;">{diff} 부족</span>'


# ── 메인 렌더링 ──────────────────────────────────────────

def render_stress_report(cfg: AppConfig):
    """Viewer용 Provider별 부하 테스트 보고서."""

    # 테스트된 provider 목록 + 키 풀 정보
    tested = list_tested_providers(cfg)
    key_info_list = get_provider_key_info(cfg)
    key_map = {k["provider"]: k for k in key_info_list}

    # 알려진 provider만 표시 (META에 등록 + 키 또는 테스트 데이터 존재)
    known = set(_PROVIDER_META.keys())
    relevant = (set(tested) | set(key_map.keys())) & known
    all_providers = sorted(relevant)

    if not all_providers:
        st.info("등록된 API 키와 테스트 결과가 없습니다.")
        return

    # ── 전체 요약 (모든 provider 카드) ──
    st.subheader("서비스별 동시 사용 권장")

    # 모든 provider 분석
    all_reports: dict[str, ProviderReport] = {}
    for prov in all_providers:
        ki = key_map.get(prov, {})
        rounds_raw = list_stress_rounds_by_provider(cfg, prov) if prov in tested else []
        rounds = _parse_rounds(rounds_raw)

        if rounds:
            rec, grade, comment = _compute_recommendation(prov, rounds)
        else:
            rec, grade, comment = 0, "-", "테스트 미실시"

        all_reports[prov] = ProviderReport(
            provider=prov,
            rounds=rounds,
            recommended_users=rec,
            grade=grade,
            comment=comment,
            key_count=ki.get("key_count", 0),
            total_concurrency=int(ki.get("total_concurrency", 0) or 0),
        )

    # 요약 카드 그리드 — 행 단위로 균등 배분
    n = len(all_providers)
    per_row = 3 if n in (3, 5, 6) else 2 if n in (2, 4) else min(n, 3)
    rows_list = [all_providers[i:i + per_row] for i in range(0, n, per_row)]

    for row_items in rows_list:
        cols = st.columns(len(row_items))
        for col, prov in zip(cols, row_items):
            rpt = all_reports[prov]
            with col:
                if rpt.grade == "-":
                    border_color = "#555"
                    num_color = "#888"
                else:
                    border_color, _ = _GRADE_COLORS.get(rpt.grade, ("#95a5a6", "#f0f0f0"))
                    num_color = border_color

                output_type = _provider_output_type(prov)
                has_test = rpt.grade != "-"

                if has_test:
                    grade_html = _grade_badge_html(rpt.grade)
                    rec_text = f"{rpt.recommended_users}명" if rpt.recommended_users > 0 else "0명"
                else:
                    grade_html = '<span style="color:#666;font-size:0.85em;">테스트 미실시</span>'
                    rec_text = "-"

                opacity = "opacity:0.5;" if not has_test else ""
                card_html = (
                    f'<div style="background:linear-gradient(135deg,#1a1a2e 0%,#16213e 100%);'
                    f'border:2px solid {border_color};border-radius:12px;padding:16px;'
                    f'text-align:center;margin-bottom:8px;{opacity}">'
                    f'<div style="font-size:0.85em;color:#a0a0b8;margin-bottom:2px;">'
                    f'{_provider_label(prov)}'
                    f'<span style="font-size:0.8em;color:#666;"> · {output_type}</span></div>'
                    f'<div style="font-size:2.2em;font-weight:800;color:{num_color};margin:6px 0;">'
                    f'{rec_text}</div>'
                    f'<div style="margin-bottom:6px;">{grade_html}</div>'
                    f'<div style="font-size:0.75em;color:#888;margin-top:4px;">'
                    f'동시수용 {rpt.total_concurrency}명</div>'
                    f'</div>'
                )
                st.markdown(card_html, unsafe_allow_html=True)

    st.caption("등급 기준 — A: 성공률 95%이상 & 지연 기준 이하 | B: 성공률 80%이상 & 지연 기준 이하 | C: 기타  (지연 기준은 결과물 유형별 차등 적용)")

    st.divider()

    # ── Provider 선택 → 상세 보기 ──
    provider_options = {p: _provider_label(p) for p in all_providers}
    selected = st.selectbox(
        "상세 보기",
        options=list(provider_options.keys()),
        format_func=lambda p: provider_options[p],
        key="report_provider_select",
    )

    if not selected:
        return

    rpt = all_reports[selected]
    _render_provider_detail(rpt)


def _render_provider_detail(rpt: ProviderReport):
    """선택된 provider의 상세 보고서."""
    meta = _get_meta(rpt.provider)
    fg, _ = _GRADE_COLORS.get(rpt.grade, ("#95a5a6", "#f0f0f0"))
    output_type = _provider_output_type(rpt.provider)

    # ── 권장 사항 ──
    a_sec = meta['grade_a_ms'] / 1000
    b_sec = meta['grade_b_ms'] / 1000
    rec_html = (
        f'<div style="background:linear-gradient(135deg,#1a1a2e 0%,#16213e 100%);'
        f'border-left:4px solid {fg};border-radius:8px;padding:16px 20px;margin:8px 0 16px 0;">'
        f'<div style="font-size:1.1em;font-weight:700;color:#f0f0f0;margin-bottom:8px;">'
        f'{_provider_label(rpt.provider)} 권장 사항'
        f'<span style="font-size:0.75em;color:#888;font-weight:400;margin-left:8px;">'
        f'결과물: {output_type} · 지연 기준: A ≤ {a_sec:.0f}초, B ≤ {b_sec:.0f}초</span></div>'
        f'<div style="font-size:0.95em;color:#c0c0d0;line-height:1.6;">{rpt.comment}</div>'
        f'</div>'
    )
    st.markdown(rec_html, unsafe_allow_html=True)

    if not rpt.rounds:
        st.warning(
            f"이 provider의 부하 테스트가 아직 실시되지 않았습니다.  \n"
            f"관리자에게 **{_provider_label(rpt.provider)}** 부하 테스트를 요청해주세요."
        )
        return

    # ── 키 풀 현황 ──
    st.markdown("**키 풀 현황**")
    diff = rpt.total_concurrency - rpt.recommended_users
    tbl = (
        '<table style="width:100%;border-collapse:collapse;font-size:0.9em;margin-bottom:16px;">'
        '<tr style="border-bottom:1px solid #444;">'
        f'<td style="padding:8px;color:#a0a0b8;">보유 키 수</td>'
        f'<td style="padding:8px;font-weight:600;">{rpt.key_count}개</td>'
        f'<td style="padding:8px;color:#a0a0b8;">총 동시수용</td>'
        f'<td style="padding:8px;font-weight:600;">{rpt.total_concurrency}명</td>'
        f'<td style="padding:8px;color:#a0a0b8;">테스트 권장</td>'
        f'<td style="padding:8px;font-weight:600;">{rpt.recommended_users}명</td>'
        f'<td style="padding:8px;color:#a0a0b8;">판정</td>'
        f'<td style="padding:8px;">{_capacity_html(diff)}</td>'
        '</tr></table>'
    )
    st.markdown(tbl, unsafe_allow_html=True)

    # ── 차트: 성공률 & 지연시간 ──
    st.markdown("**성능 추이**")
    chart_data = []
    for r in rpt.rounds:
        chart_data.append({
            "사용자수": r.num_users,
            "성공률(%)": r.success_rate,
            "평균지연(ms)": r.avg_latency_ms,
            "P95(ms)": r.p95_ms,
        })

    df = pd.DataFrame(chart_data)

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("성공률 (%)")
        st.line_chart(df, x="사용자수", y="성공률(%)")
    with col2:
        st.markdown("지연시간 (ms)")
        st.line_chart(df, x="사용자수", y=["평균지연(ms)", "P95(ms)"])

    # ── 단계별 결과 테이블 ──
    st.markdown("**단계별 상세**")
    table_rows = []
    for r in rpt.rounds:
        icon = _status_icon(rpt.provider, r.success_rate, r.avg_latency_ms)
        table_rows.append({
            "판정": icon,
            "동시 사용자": f"{r.num_users}명",
            "성공": r.successes,
            "실패": r.failures,
            "성공률(%)": r.success_rate,
            "평균지연(ms)": r.avg_latency_ms,
            "P95(ms)": r.p95_ms,
            "P99(ms)": r.p99_ms,
            "테스트 일시": r.tested_at,
        })
    st.dataframe(
        pd.DataFrame(table_rows),
        hide_index=True,
        width="stretch",
    )

    # ── 테스트 조건 ──
    is_mock = rpt.rounds[0].is_mock if rpt.rounds else True
    st.caption(
        f"모드: {'Mock (시뮬레이션)' if is_mock else 'Real (실제 API)'}  |  "
        f"최근 테스트: {rpt.rounds[-1].tested_at if rpt.rounds else '-'}"
    )
