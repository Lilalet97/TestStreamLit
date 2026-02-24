# Generative AI Multi-API Tester

Streamlit 기반 멀티 테넌트 생성형 AI API 테스트 플랫폼.
Midjourney(LegNext), Kling AI 등 다양한 API를 통합 UI에서 테스트하고 관리한다.

---

## 프로젝트 구조

```
├── app.py                        # Streamlit 앱 진입점
├── requirements.txt              # Python 의존성
├── run_streamlit_app.bat         # Windows 원클릭 실행 스크립트
│
├── core/                         # 핵심 비즈니스 로직
│   ├── analysis.py               # GPT 오류 분석 (로컬 패턴 + OpenAI)
│   ├── auth.py                   # 인증 (PBKDF2 해싱, 세션, 쿠키)
│   ├── config.py                 # 설정 로더 (secrets/env/tenant)
│   ├── database.py               # DB 커넥션 래퍼 (SQLite/libSQL)
│   ├── db.py                     # DB 오퍼레이션 (runs, active_jobs, users)
│   ├── http.py                   # HTTP 유틸 (POST/GET JSON)
│   ├── key_pool.py               # API 키 풀링 (FIFO 큐, 동시성, RPM 제한)
│   └── redact.py                 # 민감 데이터 마스킹
│
├── providers/                    # API 프로바이더
│   ├── legnext.py                # LegNext (Midjourney) 클라이언트
│   └── kling.py                  # Kling AI 클라이언트 (JWT 인증)
│
├── ui/                           # UI 컴포넌트
│   ├── admin_page.py             # 관리자 대시보드
│   ├── auth_page.py              # 로그인/부트스트랩 페이지
│   ├── registry.py               # 탭 레지스트리 (동적 필터링)
│   ├── result_store.py           # 세션 기반 결과 저장소
│   ├── run_detail.py             # 실행 상세 모달
│   ├── sidebar.py                # 사이드바 (프로필, 이력, 테스트 모드)
│   └── tabs/
│       ├── legnext_tab.py        # Midjourney(LegNext) 생성 탭
│       ├── kling_tab.py          # Kling AI 생성 탭
│       ├── naver_tab.py          # 네이버 디자인 커스텀 컴포넌트
│       ├── mj_tab.py             # Midjourney 디자인 커스텀 컴포넌트
│       └── templates/            # HTML 템플릿
│           ├── mj.html
│           └── naver.html
│
├── tenants/                      # 멀티 테넌트 설정
│   ├── default.json
│   └── school_a.json
│
└── Sources/                      # 정적 에셋 (로고 등)
```

---

## 주요 기능

### 탭 시스템

| 탭 | 설명 |
|----|------|
| **Midjourney (LegNext)** | LegNext API를 통한 Midjourney 이미지 생성. 파라미터(`--ar`, `--v`, `--q`, `--s` 등) 지원, 자동 폴링, 키 풀 연동 |
| **Kling AI** | Kling AI 이미지/비디오 생성. 모델 선택, 비디오 길이(5s/10s), 창의성 슬라이더 |
| **네이버** | 네이버 메인 페이지 디자인 재현 (HTML/CSS/JS 커스텀 컴포넌트) |
| **Midjourney** | Midjourney `/imagine` 페이지 디자인 재현 (HTML/CSS/JS 커스텀 컴포넌트) |

탭 노출은 테넌트별 `enabled_features` 설정으로 제어된다.

### 인증

- **PBKDF2-SHA256** 패스워드 해싱 (200k iterations)
- **쿠키 기반 세션** 유지 (24시간 TTL, F5 복원)
- **부트스트랩 모드**: 최초 실행 시 관리자 계정 생성
- **환경변수 시딩**: `ADMIN_USER`/`ADMIN_PASS`로 자동 관리자 생성

### 키 풀 관리

- **FIFO 큐**: 요청 순서대로 키 할당
- **동시성 제한**: 키별 최대 동시 사용 수
- **RPM 제한**: 분당 요청 수 제어
- **우선순위/테넌트 스코프**: 키별 우선순위 및 테넌트 제한
- **리스 시스템**: 하트비트 + TTL 자동 만료

### 동시성 제어

- **유저 레벨**: `USER_MAX_CONCURRENCY` (기본 1)
- **글로벌**: `GLOBAL_MAX_CONCURRENCY` (기본 4)
- `active_jobs` 테이블로 실시간 추적, TTL 기반 정리

### 오류 분석

- **로컬 분석**: HTTP 상태 코드 패턴 매칭 (402→할당량, 401→인증, 429→속도 제한)
- **GPT 분석**: OpenAI API로 상세 분석 (요약, 원인, 조치, 확인 사항)

### 관리자 대시보드

- **모니터링**: 실시간 활성 작업 (1초 갱신)
- **실행 이력**: 유저별 필터, 페이지네이션
- **키 풀 상태**: 대기열 + 활성 리스
- **사용자 관리**: 추가/수정/삭제, 역할/학교 변경, 비밀번호 초기화

### 멀티 테넌시

- 테넌트별 JSON 설정 (`tenants/*.json`)
- 커스텀 브랜딩 (페이지 제목, 로고)
- 피처 플래그로 탭 노출 제어
- 키 풀 테넌트 스코프

### 테스트 모드 (Mock)

사이드바에서 MOCK 토글 → 실제 API 호출 없이 UI 테스트.
시나리오: SUCCESS, FAILED_402, FAILED_401, FAILED_429, SERVER_500, TIMEOUT

---

## 실행 방법

### Windows (권장)

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

`.streamlit/secrets.toml` 파일을 생성하거나 환경변수로 설정한다.

```toml
# API 키
MJ_API_KEY = "sk-legnext-xxx"
KLING_ACCESS_KEY = "ak-kling-xxx"
KLING_SECRET_KEY = "sk-kling-xxx"

# (선택) OpenAI 오류 분석
OPENAI_API_KEY = "sk-openai-xxx"
OPENAI_MODEL = "gpt-4o-mini"

# (선택) 관리자 자동 생성
ADMIN_USER = "admin"
ADMIN_PASS = "password123"

# (선택) 키 풀
KEY_POOL_JSON = '{"legnext": [...], "kling": [...]}'

# (선택) 동시성 제한
USER_MAX_CONCURRENCY = "2"
GLOBAL_MAX_CONCURRENCY = "10"

# (선택) Turso 원격 DB
TURSO_DATABASE_URL = "libsql://your-db.turso.io"
TURSO_AUTH_TOKEN = "eyJ..."
```

---

## 데이터베이스

SQLite(`runs.db`)를 기본으로 사용하며, Turso(libSQL) 원격 동기화를 선택적으로 지원한다.

| 테이블 | 용도 |
|--------|------|
| `runs` | API 실행 로그 (요청/응답/분석) |
| `active_jobs` | 동시성 추적 |
| `users` | 사용자 계정 |
| `user_sessions` | 로그인 세션 |
| `api_keys` | 키 풀 정의 |
| `api_key_leases` | 활성 키 할당 |
| `api_key_usage_minute` | RPM 추적 |
| `api_key_waiters` | FIFO 대기열 |

---

## 의존성

```
streamlit
requests
PyJWT
streamlit-cookies-controller
libsql-experimental              # Linux/Mac 전용 (선택, Turso 동기화)
```

> `libsql-experimental`은 Windows에서 미지원 — 로컬 SQLite로 자동 폴백.
