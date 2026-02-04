# core/analysis.py
import json
import re
from typing import Optional, Any, Dict

from core.redact import redact_obj, json_dumps_safe
from core.config import AppConfig

# Optional: OpenAI SDK
try:
    from openai import OpenAI
except Exception:
    OpenAI = None


def local_analyze_error(http_status: Optional[int], response_json: Optional[dict], response_text: Optional[str]):
    msg = ""
    if isinstance(response_json, dict):
        if "message" in response_json:
            msg = str(response_json.get("message") or "")
        if not msg and isinstance(response_json.get("error"), dict):
            msg = str((response_json["error"].get("message") or ""))

    text = (response_text or "")[:2000]

    def pack(summary, causes, fixes, checks):
        return {
            "summary": summary,
            "likely_causes": causes,
            "recommended_fixes": fixes,
            "next_checks": checks,
        }

    if http_status == 402 or "insufficient quota" in (msg.lower() + text.lower()):
        return pack(
            "크레딧(Quota) 부족으로 요청이 거절되었습니다.",
            ["계정 크레딧이 0이거나 플랜 제한에 도달"],
            ["대시보드에서 크레딧 충전/플랜 업그레이드", "올바른 워크스페이스의 키인지 확인"],
            ["잔액 확인", "키가 연결된 프로젝트 확인"]
        )
    if http_status == 401:
        return pack(
            "인증 실패(401)입니다.",
            ["API 키 누락/오타", "헤더 키 이름 불일치(x-api-key 등)"],
            ["키 재확인", "요구 헤더 형식 확인"],
            ["요청 헤더 로그 확인(마스킹 상태로)"]
        )
    if http_status == 429:
        return pack(
            "레이트 리밋(429)으로 제한되었습니다.",
            ["짧은 시간에 요청 과다", "계정 제한이 낮음"],
            ["백오프/재시도 적용", "요청 빈도 제한", "플랜 업그레이드"],
            ["요청 횟수/분당 제한 확인"]
        )
    if http_status and http_status >= 500:
        return pack(
            "서버 오류(5xx) 가능성이 큽니다.",
            ["일시적 서버 장애", "업스트림 장애"],
            ["짧게 재시도(1~2회)", "시간 두고 재시도"],
            ["상태 페이지/공지 확인"]
        )

    return pack(
        "원인을 특정하기 어렵습니다(로그 추가 확인 필요).",
        ["응답 포맷이 예상과 다름", "입력 파라미터 검증 실패"],
        ["response_json/response_text를 확인"],
        ["요청 payload 파라미터를 최소화해 재현"]
    )


def _analysis_schema_default(summary: str):
    return {
        "summary": summary,
        "likely_causes": [],
        "recommended_fixes": [],
        "next_checks": [],
    }


def is_valid_analysis(obj) -> bool:
    if not isinstance(obj, dict):
        return False
    required = ["summary", "likely_causes", "recommended_fixes", "next_checks"]
    if any(k not in obj for k in required):
        return False
    if not isinstance(obj["summary"], str) or not obj["summary"].strip():
        return False
    for k in ["likely_causes", "recommended_fixes", "next_checks"]:
        if not isinstance(obj[k], list):
            return False
    return True


def _extract_json_object(text: str):
    if not text:
        return None

    t = text.strip()
    t = re.sub(r"^```json\s*", "", t, flags=re.IGNORECASE)
    t = re.sub(r"^```\s*", "", t)
    t = re.sub(r"\s*```$", "", t)

    try:
        return json.loads(t)
    except Exception:
        pass

    m = re.search(r"\{[\s\S]*\}", t)
    if not m:
        return None

    candidate = m.group(0)
    try:
        return json.loads(candidate)
    except Exception:
        return None


def gpt_analyze_error(cfg: AppConfig, provider: str, operation: str, endpoint: str, request_obj: dict,
                      http_status: Optional[int], response_text: Optional[str],
                      response_json: Optional[dict], exception_text: Optional[str]) -> Optional[Dict[str, Any]]:
    if not cfg.openai_api_key or OpenAI is None:
        return None

    client = OpenAI(api_key=cfg.openai_api_key)

    safe_req = redact_obj(request_obj or {})
    safe_resp_json = redact_obj(response_json) if isinstance(response_json, dict) else response_json

    user_text = (
        "당신은 API 디버깅 도우미입니다. 아래 로그만 보고 원인과 해결책을 한국어로 정리하세요.\n\n"
        "반드시 아래 JSON만 출력하세요. 다른 텍스트/마크다운/코드펜스 금지.\n"
        "{\n"
        '  "summary": "한 줄 요약",\n'
        '  "likely_causes": ["가능 원인 1", "가능 원인 2"],\n'
        '  "recommended_fixes": ["권장 조치 1", "권장 조치 2"],\n'
        '  "next_checks": ["추가 확인 1", "추가 확인 2"]\n'
        "}\n\n"
        "조건:\n"
        "- 확신이 낮으면 '가능성'으로 표현\n"
        "- 보안상 민감정보(키/토큰)는 언급하지 말 것\n\n"
        f"[provider] {provider}\n"
        f"[operation] {operation}\n"
        f"[endpoint] {endpoint}\n"
        f"[http_status] {http_status}\n"
        f"[exception] {exception_text}\n\n"
        f"[request]\n{json_dumps_safe(safe_req)}\n\n"
        f"[response_text]\n{(response_text or '')[:3000]}\n\n"
        f"[response_json]\n{json_dumps_safe(safe_resp_json)}\n"
    )

    try:
        resp = client.responses.create(
            model=cfg.openai_model,
            input=[{"role": "user", "content": [{"type": "text", "text": user_text}]}],
            temperature=0.2,
        )
        txt = ""
        try:
            txt = resp.output_text or ""
        except Exception:
            txt = ""
        if not txt:
            txt = str(resp)

        parsed = _extract_json_object(txt)
        if is_valid_analysis(parsed):
            return parsed
        return _analysis_schema_default("GPT 분석 결과가 JSON 형식으로 파싱되지 않았습니다(로컬 분석 권장).")
    except Exception as e:
        return _analysis_schema_default(f"GPT 분석 호출 실패: {e}")


def analyze_error(cfg: AppConfig, provider: str, operation: str, endpoint: str, request_obj: dict,
                  http_status: Optional[int], response_text: Optional[str],
                  response_json: Optional[dict], exception_text: Optional[str]) -> Dict[str, Any]:
    analysis = gpt_analyze_error(cfg, provider, operation, endpoint, request_obj, http_status, response_text, response_json, exception_text)
    if analysis is None or not is_valid_analysis(analysis):
        analysis = local_analyze_error(http_status, response_json if isinstance(response_json, dict) else None, response_text)
    if not is_valid_analysis(analysis):
        analysis = _analysis_schema_default("분석 생성 실패(알 수 없는 오류).")
    return analysis
