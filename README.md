# Generative AI Multi-API Education Platform

Streamlit 기반 멀티 테넌트 생성형 AI API 교육 플랫폼.
OpenAI, Google Gemini, Google Veo, Kling AI, ElevenLabs 등 다양한 API를 통합 UI에서 체험하고 관리한다.

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
│   ├── google_imagen.py            # Google Gemini 이미지 생성/편집 (Vertex AI)
│   ├── google_veo.py               # Google Veo 비디오 생성 (Vertex AI)
│   ├── vertex_auth.py              # Vertex AI 공유 인증 (SA JSON → OAuth2 토큰)
│   ├── kling.py                    # Kling AI 비디오 생성 (JWT 인증)
│   └── legnext.py                  # LegNext (Midjourney) — 보존 (향후 재전환용)
│
├── ui/                             # UI 컴포넌트
│   ├── admin_page.py               # 관리자/열람자 대시보드 (사이드바 탭 전환)
│   ├── auth_page.py                # 로그인 / 부트스트랩 페이지
│   ├── floating_chat.py            # 플로팅 채팅 위젯
│   ├── registry.py                 # 탭 레지스트리 (동적 필터링)
│   ├── result_store.py             # 세션 기반 결과 저장소
│   ├── sidebar.py                  # 사이드바 (프로필 카드, 테스트 모드)
│   ├── stress_report.py            # 부하 테스트 리포트 (Provider별 성적표)
│   ├── stress_test_tab.py          # 부하 테스트 실행/결과 UI
│   └── tabs/                       # API 체험 탭 (JS 커스텀 컴포넌트)
│       ├── gpt_tab.py              # GPT 대화
│       ├── mj_tab.py               # Midjourney 이미지 생성 (Google Gemini 백엔드)
│       ├── kling_tab.py            # 비디오 생성 (Kling API 백엔드)
│       ├── kling_veo_tab.py        # 비디오 생성 (Google Veo 백엔드)
│       ├── elevenlabs_tab.py       # ElevenLabs TTS 음성 생성
│       ├── nanobanana_tab.py       # NanoBanana 이미지 생성/편집 (Google Gemini)
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

| 탭 | 백엔드 | 설명 |
|----|--------|------|
| **GPT** | OpenAI | 멀티턴 대화, 모델 선택, 대화 이력 관리 |
| **Midjourney** | Google Gemini (Vertex AI) | 이미지 생성, MJ 파라미터(`--ar`, `--v`, `--s` 등) 지원, 갤러리 |
| **Kling** | Kling AI (JWT) | 텍스트→비디오, 모델/비율/해상도/길이 선택 |
| **Kling (Veo)** | Google Veo (Vertex AI) | 텍스트→비디오, Veo 설정 자동 변환 |
| **ElevenLabs** | ElevenLabs | TTS 음성 생성, 보이스 선택, 안정성/유사도 조절 |
| **NanoBanana** | Google Gemini (Vertex AI) | 이미지 생성/편집, 멀티턴 세션, 개별 이미지 편집 |
| **Suno** | Suno | 음악 생성 (계정 배정 기반) |

> Kling 탭과 Kling(Veo) 탭은 동일한 UI를 공유하며, 백엔드 API만 다르다.
> 테넌트 설정에서 `tab.kling` / `tab.kling_veo`를 개별 제어할 수 있다.

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

### Vertex AI 인증

Google Gemini(이미지)와 Google Veo(비디오) API는 **Vertex AI** 방식으로 인증한다.

- **Service Account JSON** → OAuth2 Bearer 토큰 자동 발급/갱신
- 토큰은 `providers/vertex_auth.py`에서 캐시 관리 (1시간 만료 시 자동 갱신)
- `KEY_POOL_JSON`에서 `"sa_json": "__VERTEX__"` 마커 사용 시 `VERTEX_SA_JSON` 시크릿에서 자동 참조
- `google_imagen`(NanoBanana/MJ)과 `google_veo`(Kling Veo탭)는 별도 provider로 RPM 독립 운영

### 부하 테스트

- **Plan 기반 burst 테스트**: Provider × 동시 사용자 수 조합
- **Mock / Real 모드**: API 미호출 테스트 가능
- **실시간 진행 표시**: 라운드별 진행률, 메트릭
- **Provider별 성적표**: 평균 지연시간, 성공률, P95/P99, 등급 산정
- **비교 차트**: 사용자 수별 지연시간/성공률 추이

### 관리자 대시보드 (사이드바 탭 전환, 6개 메뉴)

| 메뉴 | 기능 |
|------|------|
| **모니터링** | 실시간 활성 작업 (1초 갱신) |
| **키풀 상태** | 대기열, 활성 리스, 유저별 사용 현황 |
| **실행 이력** | 유저별 필터, GPT/MJ/Kling/ElevenLabs/NanoBanana 기록, 상세 보기 |
| **부하 테스트** | 설정/실행/결과 조회 |
| **계정 관리** | 추가/수정/삭제, 역할/학교/Suno 계정 배정 |
| **DB 관리** | 테이블별 레코드 현황, 수동 삭제, 자동 삭제 주기 설정 |

### 열람자(viewer) 대시보드 (사이드바 탭 전환, 3개 메뉴)

| 메뉴 | 기능 |
|------|------|
| **모니터링** | 실시간 활성 작업 (읽기 전용) |
| **실행 이력** | 유저별 필터, 상세 보기 (읽기 전용) |
| **부하테스트 결과** | 과거 부하 테스트 결과 조회 |

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
    {"name": "imagen-1", "sa_json": "__VERTEX__", "project_id": "__VERTEX__", "location": "__VERTEX__", "concurrency_limit": 4, "rpm_limit": 60, "priority": 10, "tenant_scope": "*", "is_active": true}
  ],
  "google_veo": [
    {"name": "veo-1", "sa_json": "__VERTEX__", "project_id": "__VERTEX__", "location": "__VERTEX__", "concurrency_limit": 2, "rpm_limit": 30, "priority": 10, "tenant_scope": "*", "is_active": true}
  ]
}
"""

# ── 개별 키 (KEY_POOL_JSON 없을 때 fallback) ──
OPENAI_API_KEY = "sk-..."
OPENAI_MODEL = "gpt-4o-mini"
ELEVENLABS_API_KEY = "sk_..."

# ── Vertex AI (google_imagen, google_veo 공용) ──
VERTEX_PROJECT_ID = "your-gcp-project-id"
VERTEX_LOCATION = "us-central1"
VERTEX_SA_JSON = '{"type":"service_account","project_id":"...","private_key":"-----BEGIN PRIVATE KEY-----\\n...\\n-----END PRIVATE KEY-----\\n","client_email":"...@...iam.gserviceaccount.com",...}'

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

> **`__VERTEX__` 마커**: KEY_POOL_JSON 내 `sa_json`, `project_id`, `location` 값을 `"__VERTEX__"`로 설정하면
> 별도의 `VERTEX_SA_JSON`, `VERTEX_PROJECT_ID`, `VERTEX_LOCATION` 시크릿에서 자동으로 참조한다.
> SA JSON은 길이가 길어 KEY_POOL_JSON에 직접 넣기 어렵기 때문에 이 방식을 권장한다.

### Vertex AI 설정 (Google Gemini / Veo)

1. GCP Console → **IAM 및 관리자** → **서비스 계정** → 서비스 계정 만들기
2. 역할: **Vertex AI 사용자** (`roles/aiplatform.user`) 부여
3. **키** 탭 → 새 키 만들기 → JSON 다운로드
4. GCP Console → **API 및 서비스** → **Vertex AI API** 사용 설정
5. 다운로드한 JSON 파일의 내용을 `VERTEX_SA_JSON`에 입력 (한 줄, `\n` → `\\n` 이스케이프)
6. JSON 내 `project_id`를 `VERTEX_PROJECT_ID`에 입력

### 테넌트 설정

`tenants/*.json` 파일로 학교별 설정을 관리한다.

```json
{
  "tenant_id": "school_a",
  "layout": "A 대학교",
  "enabled_features": [
    "tab.gpt", "tab.mj", "tab.suno", "tab.kling", "tab.kling_veo",
    "tab.elevenlabs", "tab.nanobanana",
    "mj.create", "mj.imagine", "mj.explore",
    "kling.text2video", "kling.assets",
    "elevenlabs.tts", "elevenlabs.gallery",
    "nanobanana.generate", "nanobanana.gallery"
  ],
  "branding": {
    "page_title": "A 대학교 AI 교육",
    "browser_tab_title": "A 대학교",
    "logo_path": "tenants/logos/school_a.png"
  }
}
```

> `tab.kling`과 `tab.kling_veo`는 독립적으로 제어된다. 둘 다 활성화하면 사용자에게 Kling 탭이 2개 표시된다.

---

## 수동 관리 항목

운영 중 파일 편집이 필요한 항목:

| 항목 | 파일 | 빈도 |
|------|------|------|
| 학교 추가 | `tenants/*.json` + `tenants/logos/` | 필요 시 |
| API 키 추가/변경 | `secrets.toml` (KEY_POOL_JSON) | 필요 시 |
| Vertex AI 인증 갱신 | `secrets.toml` (VERTEX_SA_JSON) | SA 키 만료 시 |
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
| `kling_web_history` | Kling 비디오 기록 (Kling API / Veo 공유) |
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
google-auth                      # Vertex AI OAuth2 인증
PyJWT
streamlit-cookies-controller
pandas
libsql-experimental              # Linux/Mac 전용 (Turso 동기화)
```

> `libsql-experimental`은 Windows에서 미지원 — 로컬 SQLite로 자동 폴백.
