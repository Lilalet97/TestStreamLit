# Generative AI Multi-API Education Platform

Streamlit 기반 멀티 테넌트 생성형 AI API 교육 플랫폼.
OpenAI, Midjourney, Kling AI, ElevenLabs, Google Imagen 등 다양한 API를 통합 UI에서 체험하고 관리한다.

---

## 프로젝트 구조

```
├── app.py                          # Streamlit 앱 진입점
├── requirements.txt                # Python 의존성
├── run_streamlit_app.bat           # Windows 원클릭 실행 스크립트
│
├── core/                           # 핵심 비즈니스 로직
│   ├── analysis.py                 # GPT 오류 분석 (로컬 패턴 + OpenAI)
│   ├── api_bridge.py               # 탭-API 브릿지 (call_with_lease 헬퍼)
│   ├── auth.py                     # 인증 (PBKDF2 해싱, 세션, 쿠키)
│   ├── config.py                   # 설정 로더 (secrets/env/tenant)
│   ├── database.py                 # DB 커넥션 래퍼 (SQLite/libSQL + Turso 동기화)
│   ├── db.py                       # DB 스키마 + CRUD (전체 테이블)
│   ├── http.py                     # HTTP 유틸 (POST/GET JSON)
│   ├── key_pool.py                 # API 키 풀링 (FIFO 큐, 동시성, RPM 제한)
│   ├── redact.py                   # 민감 데이터 마스킹
│   └── stress_test.py              # 부하 테스트 엔진 (Plan 기반 burst)
│
├── providers/                      # API 프로바이더 클라이언트
│   ├── elevenlabs.py               # ElevenLabs TTS
│   ├── google_imagen.py            # Google Imagen (NanoBanana)
│   ├── kling.py                    # Kling AI 비디오 (JWT 인증)
│   └── legnext.py                  # LegNext (Midjourney) 이미지
│
├── ui/                             # UI 컴포넌트
│   ├── admin_page.py               # 관리자 대시보드 (6개 탭)
│   ├── auth_page.py                # 로그인 / 부트스트랩 페이지
│   ├── floating_chat.py            # 플로팅 채팅 위젯
│   ├── registry.py                 # 탭 레지스트리 (동적 필터링)
│   ├── result_store.py             # 세션 기반 결과 저장소
│   ├── run_detail.py               # 실행 상세 모달
│   ├── sidebar.py                  # 사이드바 (프로필, 이력, 테스트 모드)
│   ├── stress_report.py            # 부하 테스트 리포트 (Provider별 성적표)
│   ├── stress_test_tab.py          # 부하 테스트 실행/결과 UI
│   └── tabs/                       # API 체험 탭 (JS 커스텀 컴포넌트)
│       ├── gpt_tab.py              # GPT 대화
│       ├── mj_tab.py               # Midjourney 이미지 생성
│       ├── kling_web_tab.py        # Kling AI 비디오 생성
│       ├── elevenlabs_tab.py       # ElevenLabs TTS 음성 생성
│       ├── nanobanana_tab.py       # NanoBanana (Google Imagen) 이미지
│       ├── suno_tab.py             # Suno 음악 생성
│       └── templates/              # HTML/JS 커스텀 컴포넌트
│           ├── gpt/index.html
│           ├── mj/index.html
│           ├── kling/index.html
│           ├── elevenlabs/index.html
│           └── nanobanana/index.html
│
├── tenants/                        # 멀티 테넌트 설정
│   ├── default.json
│   ├── school_a.json
│   └── logos/                      # 학교별 로고 이미지
│
└── Sources/                        # 정적 에셋 (공통 로고 등)
```

---

## 주요 기능

### API 체험 탭

| 탭 | Provider | 설명 |
|----|----------|------|
| **GPT** | OpenAI | 멀티턴 대화, 모델 선택, 대화 이력 관리 |
| **Midjourney** | LegNext API | 이미지 생성, 파라미터(`--ar`, `--v`, `--s` 등), 갤러리 |
| **Kling AI** | Kling | 텍스트→비디오, 시작/종료 프레임, 모드/해상도 선택 |
| **ElevenLabs** | ElevenLabs | TTS 음성 생성, 보이스 선택, 안정성/유사도 조절 |
| **NanoBanana** | Google Imagen | 이미지 생성/편집, 멀티턴 세션, 스타일 프리셋 |
| **Suno** | Suno | 음악 생성 (계정 배정 기반) |

모든 탭은 **declare_component** 기반 HTML/JS 커스텀 컴포넌트로 구현되어 클라이언트 사이드에서 UI를 처리하고, API 호출 시에만 서버와 통신한다.

탭 노출은 테넌트별 `enabled_features` 설정으로 제어된다.

### 인증

- **PBKDF2-SHA256** 패스워드 해싱 (200k iterations)
- **쿠키 기반 세션** 유지 (24시간 TTL, F5 복원)
- **부트스트랩 모드**: 최초 실행 시 관리자 계정 생성
- **환경변수 시딩**: `ADMIN_USER`/`ADMIN_PASS`로 자동 관리자 생성
- **역할**: admin, viewer, teacher, student

### 키 풀 관리

- **FIFO 큐**: 요청 순서대로 키 할당
- **동시성 제한**: 키별 최대 동시 사용 수 (`concurrency_limit`)
- **RPM 제한**: 분당 요청 수 제어 (`rpm_limit`)
- **우선순위/테넌트 스코프**: 키별 우선순위 및 테넌트 제한
- **리스 시스템**: 하트비트 + TTL 자동 만료
- **secrets.toml 동기화**: 앱 시작 시 `seed_keys`로 DB 반영, 삭제된 키 자동 비활성화

### 부하 테스트

- **Plan 기반 burst 테스트**: Provider × 동시 사용자 수 조합
- **Mock / Real 모드**: API 미호출 테스트 가능
- **실시간 진행 표시**: 라운드별 진행률, 메트릭
- **Provider별 성적표**: 평균 지연시간, 성공률, P95/P99, 등급 산정
- **비교 차트**: 사용자 수별 지연시간/성공률 추이

### 관리자 대시보드 (6개 탭)

| 탭 | 기능 |
|----|------|
| **모니터링** | 실시간 활성 작업 (1초 갱신) |
| **실행 이력** | 유저별 필터, GPT/MJ/Kling/ElevenLabs/NanoBanana 기록 |
| **키 풀 상태** | 대기열, 활성 리스, 유저별 사용 현황 |
| **사용자 관리** | 추가/수정/삭제, 역할/학교/Suno 계정 배정 |
| **부하 테스트** | 설정/실행/결과 조회 |
| **DB 관리** | 테이블별 레코드 현황, 수동 삭제, 자동 삭제 주기 설정 |

### 멀티 테넌시

- 테넌트별 JSON 설정 (`tenants/*.json`)
- 커스텀 브랜딩 (페이지 제목, 브라우저 탭 제목, 로고)
- 피처 플래그로 탭 노출 제어
- 키 풀 테넌트 스코프

### 테스트 모드 (Mock)

사이드바에서 MOCK 토글 → 실제 API 호출 없이 UI 테스트.

---

## 실행 방법

### Windows (원클릭)

```bash
run_streamlit_app.bat
```

가상환경 생성 → 의존성 설치 → `streamlit run app.py` 자동 실행.

### 수동 실행

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
streamlit run app.py
```

---

## 설정

### secrets.toml

`.streamlit/secrets.toml` 파일 또는 Streamlit Cloud의 Secrets 설정에서 관리한다.

```toml
# ── API 키 풀 (필수) ──
KEY_POOL_JSON = """
{
  "openai": [
    {"name": "openai-1", "api_key": "sk-...", "concurrency_limit": 10, "rpm_limit": 500, "priority": 10, "tenant_scope": "*", "is_active": true}
  ],
  "midjourney": [
    {"name": "mj-1", "api_key": "...", "concurrency_limit": 1, "rpm_limit": 60, "priority": 10, "tenant_scope": "*", "is_active": true}
  ],
  "kling": [
    {"name": "kling-1", "access_key": "...", "secret_key": "...", "concurrency_limit": 5, "rpm_limit": 300, "priority": 10, "tenant_scope": "*", "is_active": true}
  ],
  "elevenlabs": [
    {"name": "el-1", "api_key": "sk_...", "concurrency_limit": 2, "rpm_limit": 30, "priority": 10, "tenant_scope": "*", "is_active": true}
  ],
  "google_imagen": [
    {"name": "imagen-1", "api_key": "AI...", "concurrency_limit": 4, "rpm_limit": 20, "priority": 10, "tenant_scope": "*", "is_active": true}
  ]
}
"""

# ── 개별 키 (KEY_POOL_JSON 없을 때 fallback) ──
OPENAI_API_KEY = "sk-..."
OPENAI_MODEL = "gpt-4o-mini"
ELEVENLABS_API_KEY = "sk_..."

# ── Turso 원격 DB (Streamlit Cloud 필수) ──
TURSO_DATABASE_URL = "libsql://your-db.turso.io"
TURSO_AUTH_TOKEN = "eyJ..."

# ── Suno 계정 (선택) ──
SUNO_ACCOUNTS_JSON = """
[
  {"id": 0, "email": "", "password": "", "memo": "배정 없음"},
  {"id": 1, "email": "user@example.com", "password": "pass", "memo": "계정 1"}
]
"""

# ── 동시성 제한 (선택) ──
# USER_MAX_CONCURRENCY = "1"
# GLOBAL_MAX_CONCURRENCY = "4"
```

### 테넌트 설정

`tenants/*.json` 파일로 학교별 설정을 관리한다.

```json
{
  "tenant_id": "school_a",
  "layout": "A 대학교",
  "enabled_features": ["tab.gpt", "tab.mj", "tab.suno", "tab.kling_web", "tab.elevenlabs", "tab.nanobanana"],
  "branding": {
    "page_title": "A 대학교 AI 교육",
    "browser_tab_title": "A 대학교",
    "logo_path": "tenants/logos/school_a.png"
  }
}
```

---

## 수동 관리 항목

운영 중 파일 편집이 필요한 항목:

| 항목 | 파일 | 빈도 |
|------|------|------|
| 학교 추가 | `tenants/*.json` + `tenants/logos/` | 필요 시 |
| API 키 추가/변경 | `secrets.toml` (KEY_POOL_JSON) | 필요 시 |
| Suno 계정 추가 | `secrets.toml` (SUNO_ACCOUNTS_JSON) | 필요 시 |

그 외(사용자 관리, DB 정리, 부하 테스트 등)는 **Admin UI**에서 처리 가능.

---

## 데이터베이스

SQLite(`runs.db`)를 기본으로 사용하며, Turso(libSQL) 원격 동기화를 지원한다.

| 테이블 | 용도 |
|--------|------|
| `users` | 사용자 계정 |
| `user_sessions` | 로그인 세션 |
| `runs` | API 실행 로그 |
| `active_jobs` | 동시성 추적 |
| `api_keys` | 키 풀 정의 |
| `api_key_leases` | 활성 키 할당 |
| `api_key_usage_minute` | RPM 추적 |
| `api_key_waiters` | FIFO 대기열 |
| `gpt_conversations` | GPT 대화 이력 |
| `mj_gallery` | Midjourney 갤러리 |
| `kling_web_history` | Kling 비디오 기록 |
| `elevenlabs_history` | ElevenLabs TTS 기록 |
| `nanobanana_sessions` | NanoBanana 세션 |
| `nanobanana_history` | NanoBanana 이미지 기록 |
| `stress_test_runs` | 부하 테스트 라운드 |
| `stress_test_samples` | 부하 테스트 샘플 |
| `chat_messages` | 채팅 메시지 |
| `admin_settings` | 관리자 설정 (자동 삭제 등) |

---

## 의존성

```
streamlit
requests
PyJWT
streamlit-cookies-controller
pandas
libsql-experimental              # Linux/Mac 전용 (Turso 동기화)
```

> `libsql-experimental`은 Windows에서 미지원 — 로컬 SQLite로 자동 폴백.
