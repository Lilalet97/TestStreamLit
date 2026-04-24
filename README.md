# Generative AI Multi-API Education Platform

Streamlit 기반 멀티 테넌트 생성형 AI API 교육 플랫폼.
OpenAI, Google Gemini, Google Veo, Kling AI, xAI Grok, ElevenLabs 등 다양한 API를 통합 UI에서 체험하고 관리한다.

---

## 프로젝트 구조

```
├── app.py                          # Streamlit 앱 진입점
├── requirements.txt                # Python 의존성
├── Dockerfile                      # EC2 Docker 배포용 컨테이너 설정
├── .dockerignore                   # Docker 빌드 제외 목록
├── DEPLOY_GUIDE.txt                # EC2 배포 가이드
├── GUIDE.txt                       # 운영 가이드 (비개발자용)
├── ec2_env.list                    # EC2 환경변수 파일
├── z_run_local.bat                 # Windows 로컬 실행 스크립트
├── z_deploy.bat                    # EC2 코드 배포 스크립트
├── z_update_env.bat                # EC2 환경변수 업데이트 스크립트
├── z_deploy_all.bat                # EC2 코드+환경변수 통합 배포 스크립트
│
├── core/                           # 핵심 비즈니스 로직
│   ├── api_bridge.py               # 탭-API 브릿지 (call_with_lease 헬퍼)
│   ├── auth.py                     # 인증 (PBKDF2 해싱, 세션, 쿠키)
│   ├── config.py                   # 설정 로더 (secrets/env/tenant)
│   ├── credits.py                  # 통합 크레딧 시스템 (확인/차감)
│   ├── database.py                 # DB 커넥션 래퍼 (SQLite)
│   ├── db.py                       # DB 스키마 + CRUD (전체 테이블)
│   ├── http.py                     # HTTP 유틸 (POST/GET JSON)
│   ├── key_pool.py                 # API 키 풀링 (FIFO 큐, 동시성, RPM 제한)
│   ├── maintenance.py              # 서버 점검 관리 (KST 기반 카운트다운)
│   ├── redact.py                   # 민감 데이터 마스킹
│   ├── schedule.py                 # 수업 시간표 기반 접근 제어
│   └── stress_test.py              # 부하 테스트 엔진 (Plan 기반 burst)
│
├── providers/                      # API 프로바이더 클라이언트
│   ├── elevenlabs.py               # ElevenLabs TTS/VTV/SFX/Clone
│   ├── useapi_mj.py               # Midjourney API (useapi.net v3)
│   ├── gcs_storage.py              # Google Cloud Storage 업로드/다운로드
│   ├── google_imagen.py            # Google Gemini 이미지 생성/편집 (AI Studio)
│   ├── google_veo.py               # Google Veo 비디오 생성 (AI Studio)
│   ├── grok_video.py               # xAI Grok Imagine Video 생성
│   ├── kling.py                    # Kling AI 비디오 생성 (JWT 인증)
│   └── vertex_auth.py              # AI Studio / Vertex AI 인증 헬퍼
│
├── ui/                             # UI 컴포넌트
│   ├── admin_page.py               # 관리자/열람자 대시보드 (사이드바 탭 전환)
│   ├── auth_page.py                # 로그인 / 부트스트랩 페이지
│   ├── floating_chat.py            # 플로팅 채팅 위젯
│   ├── floating_materials.py       # 플로팅 강의자료 위젯
│   ├── floating_notice.py          # 플로팅 알림/점검 배너 (실시간 카운트다운)
│   ├── registry.py                 # 탭 레지스트리 (동적 필터링)
│   ├── result_store.py             # 세션 기반 결과 저장소
│   ├── sidebar.py                  # 사이드바 (프로필 카드, 크레딧, 테스트 모드)
│   ├── stress_report.py            # 부하 테스트 리포트 (Provider별 성적표)
│   ├── stress_test_tab.py          # 부하 테스트 실행/결과 UI
│   ├── components/                 # JS 커스텀 컴포넌트 (플로팅 요소)
│   │   ├── floating_chat/index.html
│   │   ├── floating_materials/index.html
│   │   └── floating_notice/index.html
│   └── tabs/                       # API 체험 탭 (JS 커스텀 컴포넌트)
│       ├── _nanobanana_factory.py   # NanoBanana 변형 탭 팩토리
│       ├── gpt_tab.py              # GPT 대화
│       ├── mj_tab.py               # Midjourney 이미지 생성 (Google Gemini 백엔드)
│       ├── kling_tab.py            # 비디오 생성 (Kling API 백엔드)
│       ├── kling_veo_tab.py        # 비디오 생성 (Google Veo 백엔드)
│       ├── kling_grok_tab.py       # 비디오 생성 (xAI Grok 백엔드)
│       ├── elevenlabs_tab.py       # ElevenLabs TTS 음성 생성
│       ├── nanobanana_tab.py       # NanoBanana 이미지 생성 (Imagen 4.0)
│       ├── nanobanana_2_tab.py     # NanoBanana 2 (Gemini 3.1 Flash Image)
│       ├── nanobanana_pro_tab.py   # NanoBanana Pro (Gemini 3 Pro Image)
│       ├── suno_tab.py             # Suno 음악 생성
│       ├── gallery_tab.py          # 통합 갤러리
│       ├── locked_tabs.py          # 잠금 탭 (향후 확장용)
│       └── templates/              # HTML/JS 커스텀 컴포넌트
│           ├── gpt/index.html
│           ├── mj/index.html
│           ├── kling/index.html
│           ├── kling_veo/index.html
│           ├── kling_grok/index.html
│           ├── elevenlabs/index.html
│           ├── nanobanana/index.html
│           ├── nanobanana_2/index.html
│           └── nanobanana_pro/index.html
│
├── tenants/                        # 멀티 테넌트 설정
│   ├── default.json
│   ├── hongik.json
│   ├── mokwon.json
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
| **Midjourney** | useapi.net (Midjourney API v3) | 이미지 생성, 첨부 이미지(GCS 업로드), 갤러리 |
| **NanoBanana** | Google Imagen 4.0 (AI Studio) | 이미지 생성/편집, 멀티턴 세션, 갤러리 피커(MJ+NB), 참조 이미지 |
| **NanoBanana 2** | Gemini 3.1 Flash Image (AI Studio) | NanoBanana 빠른 모델 변형 |
| **NanoBanana Pro** | Gemini 3 Pro Image (AI Studio) | NanoBanana 고품질 모델 변형 |
| **Kling** | Kling AI (JWT) | 텍스트→비디오, 모델/비율/해상도/길이 선택 |
| **Kling (Veo)** | Google Veo (AI Studio) | 텍스트→비디오, 이미지→비디오, 보간 |
| **Kling (Grok)** | xAI Grok Imagine Video | 텍스트→비디오, 이미지→비디오 |
| **ElevenLabs** | ElevenLabs | TTS, Voice-to-Voice, Sound Effects, Voice Clone/Delete |
| **Suno** | Suno | 음악 생성 (계정 배정 기반) |
| **Gallery** | — | 모든 서비스의 생성 결과 통합 갤러리 |

> Kling 탭 3종은 동일한 UI를 공유하며, 백엔드 API만 다르다.
> 테넌트 설정에서 `tab.kling` / `tab.kling_veo` / `tab.kling_grok`를 개별 제어할 수 있다.

> NanoBanana 3종은 팩토리 패턴(`_nanobanana_factory.py`)으로 생성되며, 모델과 비용만 다르다.
> 테넌트 설정에서 `tab.nanobanana` / `tab.nanobanana_2` / `tab.nanobanana_pro`를 개별 제어할 수 있다.

모든 탭은 **declare_component** 기반 HTML/JS 커스텀 컴포넌트로 구현되어 클라이언트 사이드에서 UI를 처리하고, API 호출 시에만 서버와 통신한다.

탭 노출은 테넌트별 `enabled_features` 설정으로 제어된다.

### 인증

- **PBKDF2-SHA256** 패스워드 해싱 (600k iterations)
- **쿠키 기반 세션** 유지 (24시간 TTL, F5 복원)
- **부트스트랩 모드**: 최초 실행 시 관리자 계정 생성
- **환경변수 시딩**: `ADMIN_USER`/`ADMIN_PASS`로 자동 관리자 생성
- **역할**: admin, viewer, teacher, student

### 크레딧 시스템

- **통합 잔액**: user_balance 테이블에 학생별 단일 잔액 관리
- **기능별 비용**: GPT(1), MJ(5), NanoBanana(5), NB2(5), NBPro(10), ElevenLabs(5), Kling/Veo/Grok(7)
- **면제 역할**: admin, teacher는 크레딧 차감 없음
- **비용 커스텀**: 관리자 설정에서 기능별 비용 변경 가능 (0 = 무제한)
- **사용 로그**: credit_usage_log 테이블에 차감 이력 기록

### 키 풀 관리

- **FIFO 큐**: 요청 순서대로 키 할당
- **동시성 제한**: 키별 최대 동시 사용 수 (`concurrency_limit`)
- **RPM 제한**: 분당 요청 수 제어 (`rpm_limit`)
- **RPD 제한**: 일일 요청 수 제어 (`rpd_limits`, 모델별)
- **우선순위/테넌트 스코프**: 키별 우선순위 및 테넌트 제한
- **리스 시스템**: 하트비트 + TTL 자동 만료
- **secrets.toml 동기화**: 앱 시작 시 `seed_keys`로 DB 반영, 삭제된 키 자동 비활성화

### Google AI Studio 인증

Google Gemini(이미지)와 Google Veo(비디오) API는 **AI Studio API Key** 방식으로 인증한다.

- KEY_POOL_JSON에서 `api_key` 필드에 AI Studio API 키 설정
- `google_imagen`(NanoBanana)과 `google_veo`(Kling Veo탭)는 별도 provider로 RPM 독립 운영
- `midjourney`는 useapi.net API를 통해 실제 Midjourney 호출 (첨부 이미지는 GCS 업로드 후 URL 전달)
- Vertex AI SA JSON 방식도 지원 (GCS 업로드 등에 사용)
- **PEM 호환**: SA JSON의 private_key에서 `\\n` 리터럴을 자동 변환

### 부하 테스트

- **Plan 기반 burst 테스트**: Provider × 동시 사용자 수 조합
- **Mock / Real 모드**: API 미호출 테스트 가능
- **실시간 진행 표시**: 라운드별 진행률, 메트릭
- **Provider별 성적표**: 평균 지연시간, 성공률, P95/P99, 등급(A/B/C)
- **비교 차트**: 사용자 수별 지연시간/성공률 추이

### 관리자 대시보드 (사이드바 탭 전환, 7개 메뉴)

| 메뉴 | 기능 |
|------|------|
| **알림/점검** | 알림 발송, 서버 점검 예약 (KST 카운트다운), 점검 중 로그인 차단 메시지 |
| **강의자료** | 학교별 Google Drive 폴더 연결 |
| **시간표 관리** | 수업 시간표 CRUD, 학교별 색상 구분 |
| **학교 정보** | 테넌트별 활성 탭/로고/브랜딩 조회 |
| **크레딧 관리** | 학교별/학생별 리포트, 개별/일괄 조정, 자동 충전, API 비용 추정 |
| **키풀 관리** | 프로바이더별 키 목록, 활성/비활성 토글, 동시성/RPM 현황 |
| **계정 관리** | 추가/수정/삭제, CSV 일괄 생성, 역할/학교 배정 |
| **DB 관리** | 테이블별 레코드 현황, 수동/자동 삭제, 전체 초기화 |
| **실행 이력** | 유저별 필터(DB 목록), GPT/MJ/Kling/EL/NB 기록, 상세 모달 |
| **부하 테스트** | Mock/Burst/Realistic 모드, 설정/실행/결과 조회 |

### 열람자(viewer) 대시보드 (사이드바 탭 전환, 3개 메뉴)

| 메뉴 | 기능 |
|------|------|
| **시간표** | 수업 시간표 조회, 학교별 색상 범례 |
| **크레딧 현황** | 학교별/학생별 크레딧 리포트 (읽기 전용) |
| **키풀 관리** | 프로바이더별 키 현황 (읽기 전용) |
| **실행 이력** | 유저별 필터(DB 목록), 상세 보기 (읽기 전용) |
| **부하테스트 결과** | Provider별 성적표, 동시 사용 권장 |

### 알림/점검 시스템

- **플로팅 알림 배너**: 화면 최상단 고정, position:fixed로 모든 콘텐츠 위에 표시
- **실시간 카운트다운**: 서버 점검 예정 시 JS 타이머로 매초 업데이트
- **5초 폴링**: `@st.fragment(run_every="5s")`로 서버 데이터 자동 갱신
- **단일 알림 정책**: 활성 알림은 항상 1개, 새 알림 등록 시 기존 자동 비활성화
- **닫기(dismiss)**: 사용자별 클라이언트 사이드 관리 (브라우저 세션 유지)
- **KST 기준**: 모든 시간 입력/비교는 한국 표준시 기준
- **점검 중 로그인**: 비활성화된 사용자에게 "서버 점검 중입니다" 메시지 표시

### AI 생성 로딩 오버레이

- **전체 화면 차단**: AI 생성 대기 중 사이드바 포함 전체 화면을 검은 오버레이로 덮음
- **스피너 + 메시지**: "AI 생성 중입니다. 잠시만 기다려주세요..."
- **모든 탭 지원**: GPT, MJ, NB(3종), Kling, Veo, Grok, ElevenLabs
- **자동 해제**: 생성 완료 시 pending 키 삭제 → 오버레이 자동 제거

### 이미지 다운로드

- **강제 다운로드**: 모든 이미지/비디오 다운로드를 fetch→blob→createObjectURL 방식으로 처리
- **CORS 대응**: GCS 외부 URL도 브라우저에서 직접 다운로드 (iframe 페이지 대체 방지)
- **fallback**: CORS 차단 시 window.open으로 새 창에서 열기

### 수업 시간표 접근 제어

- 다른 학교 수업 시간 중에는 갤러리 탭만 허용
- 관리자/교수는 항상 전체 접근 가능
- 테넌트별 시간표 설정으로 제어

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
z_run_local.bat
```

가상환경 생성 → 의존성 설치 → `streamlit run app.py` 자동 실행.

### 수동 실행

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
streamlit run app.py
```

### EC2 배포

```bash
# 코드만 배포
z_deploy.bat

# 환경변수만 업데이트
z_update_env.bat

# 코드 + 환경변수 모두 배포
z_deploy_all.bat
```

모든 배포 스크립트는 실행 시 자동으로 EC2 DB 백업(`docker cp`) → Docker 빌드/전송 → 컨테이너 교체를 수행한다.
컨테이너는 `-v ~/data:/app/data` 볼륨 마운트로 실행되어 DB가 컨테이너 교체 시에도 유지된다.
배포 시 `sudo chown -R 1000:1000 ~/data`로 볼륨 권한을 자동 복구한다 (`docker cp`가 root 소유로 파일을 생성하기 때문).

> 상세 배포 가이드: `DEPLOY_GUIDE.txt` 참조

---

## 설정

### secrets.toml (로컬 개발용)

`.streamlit/secrets.toml` 파일에서 관리한다.

```toml
# ── API 키 풀 (필수) ──
KEY_POOL_JSON = """
{
  "openai": [
    {"name": "openai-1", "api_key": "sk-...", "concurrency_limit": 20, "rpm_limit": 1000, "priority": 10, "tenant_scope": "*", "is_active": true}
  ],
  "midjourney": [
    {"name": "mj-1", "api_key": "useapi-bearer-token", "channel": "discord-channel-id", "concurrency_limit": 3, "rpm_limit": 10, "priority": 10, "tenant_scope": "*", "is_active": true}
  ],
  "kling": [
    {"name": "kling-1", "access_key": "...", "secret_key": "...", "concurrency_limit": 5, "rpm_limit": 300, "priority": 10, "tenant_scope": "*", "is_active": true}
  ],
  "elevenlabs": [
    {"name": "el-1", "api_key": "sk_...", "concurrency_limit": 2, "rpm_limit": 30, "priority": 10, "tenant_scope": "*", "is_active": true}
  ],
  "google_imagen": [
    {"name": "imagen-1", "api_key": "AIza...", "concurrency_limit": 5, "rpm_limit": 250, "rpd_limits": {"gemini-2.5-flash-image": 1400}, "priority": 5, "tenant_scope": "*", "is_active": true}
  ],
  "google_veo": [
    {"name": "veo-1", "api_key": "AIza...", "concurrency_limit": 2, "rpm_limit": 5, "rpd_limits": {"veo-3.1-generate-preview": 50}, "priority": 10, "tenant_scope": "*", "is_active": true}
  ],
  "grok": [
    {"name": "grok-1", "api_key": "xai-...", "concurrency_limit": 2, "rpm_limit": 60, "priority": 10, "tenant_scope": "*", "is_active": true}
  ]
}
"""

# ── 모델 설정 ──
OPENAI_MODEL = "gpt-4o-mini"
KLING_MODEL = "kling-v2.6-std"
ELEVENLABS_MODEL = "eleven_multilingual_v2"
GOOGLE_IMAGEN_MODEL = "gemini-2.5-flash-image"
GOOGLE_VEO_MODEL = "veo-3.1-generate-preview"
GROK_MODEL = "grok-imagine-video"

# ── Vertex AI SA (GCS 업로드 등에 사용) ──
VERTEX_LOCATION = "us-central1"
VERTEX_SA_JSON = '{"type":"service_account",...}'

# ── GCS (선택, 미설정 시 base64 저장) ──
GCS_BUCKET_NAME = "your-bucket-name"

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

### ec2_env.list (EC2 배포용)

EC2 Docker 컨테이너에 전달하는 환경변수 파일. `z_update_env.bat`으로 업로드한다.
secrets.toml과 동일한 키를 `KEY=VALUE` 형식으로 작성한다.

### 테넌트 설정

`tenants/*.json` 파일로 학교별 설정을 관리한다.

```json
{
  "tenant_id": "school_a",
  "layout": "A 대학교",
  "enabled_features": [
    "tab.gpt", "tab.mj", "tab.suno",
    "tab.nanobanana", "tab.nanobanana_2", "tab.nanobanana_pro",
    "nanobanana.generate", "nanobanana.gallery",
    "tab.kling", "tab.kling_veo", "tab.kling_grok",
    "kling.text2video", "kling.assets",
    "tab.elevenlabs", "elevenlabs.tts", "elevenlabs.gallery",
    "mj.create", "mj.imagine", "mj.explore"
  ],
  "branding": {
    "page_title": "A 대학교 AI 교육",
    "browser_tab_title": "A 대학교",
    "logo_path": "tenants/logos/school_a.png"
  }
}
```

> `tab.kling`, `tab.kling_veo`, `tab.kling_grok`는 독립적으로 제어된다.
> `tab.nanobanana`, `tab.nanobanana_2`, `tab.nanobanana_pro`도 독립 제어된다.

---

## 수동 관리 항목

운영 중 파일 편집이 필요한 항목:

| 항목 | 파일 | 빈도 |
|------|------|------|
| 학교 추가 | `tenants/*.json` + `tenants/logos/` | 필요 시 |
| API 키 추가/변경 | `ec2_env.list` (KEY_POOL_JSON) | 필요 시 |
| GCS 설정 | `ec2_env.list` (GCS_BUCKET_NAME, VERTEX_SA_JSON) | 최초 1회 |
| Suno 계정 추가 | `ec2_env.list` (SUNO_ACCOUNTS_JSON) | 필요 시 |

그 외(사용자 관리, DB 정리, 부하 테스트, 크레딧 관리 등)는 **Admin UI**에서 처리 가능.

---

## 데이터베이스

SQLite(`runs.db`)를 사용한다. EC2에서는 볼륨 마운트(`-v ~/data:/app/data`)로 영속성을 보장한다.

| 테이블 | 용도 |
|--------|------|
| `users` | 사용자 계정 |
| `user_sessions` | 로그인 세션 |
| `active_jobs` | 동시성 추적 |
| `api_keys` | 키 풀 정의 |
| `api_key_leases` | 활성 키 할당 |
| `api_key_usage_minute` | RPM 추적 |
| `api_key_waiters` | FIFO 대기열 |
| `gpt_conversations` | GPT 대화 이력 |
| `mj_gallery` | Midjourney 갤러리 |
| `kling_web_history` | 비디오 기록 (Kling / Veo / Grok 공유) |
| `elevenlabs_history` | ElevenLabs TTS 기록 |
| `nanobanana_sessions` | NanoBanana 세션 (3종 공유, tab_id로 구분) |
| `user_balance` | 학생별 통합 크레딧 잔액 |
| `credit_usage_log` | 크레딧 차감 이력 |
| `stress_test_runs` | 부하 테스트 라운드 |
| `stress_test_samples` | 부하 테스트 샘플 |
| `chat_messages` | 채팅 메시지 |
| `notices` | 알림 (단일 활성 정책) |
| `maintenance_schedule` | 서버 점검 예약 (KST) |
| `admin_settings` | 관리자 설정 (자동 삭제, 크레딧 비용 등) |
| `class_schedules` | 수업 시간표 (학교별, 요일/시간) |

---

## 보안

### 입력 검증
- **XSS 방지**: 모든 HTML 템플릿에서 `escHtml()` (JS) 및 `html.escape()` (Python)으로 사용자 입력 이스케이프
- **SSRF 방지**: 외부 URL 다운로드 시 scheme 검증 + private IP 차단 (`socket.getaddrinfo` + `ipaddress`)
- **입력 범위 제한**: `num_images`, `num_users` 등 숫자 파라미터에 상한/하한 적용

### API 키 보호
- **에러 메시지 마스킹**: API 호출 실패 시 에러 응답에서 API 키를 `***`로 치환
- **URL 파라미터 분리**: API 키를 URL 문자열에 포함하지 않고 `params=` 사용
- **민감 데이터 리댁션**: `core/redact.py`에서 api_key, secret, token, password, access_key, credential, private_key, client_secret 패턴 자동 마스킹
- **Kling 엔드포인트 검증**: 허용된 base URL만 호출 가능

### 크레딧 무결성
- **원자적 차감**: SQL UPDATE + WHERE 조건으로 race condition 방지
- **예약/확인 패턴**: reserve → confirm/rollback으로 실패 시 크레딧 자동 복구
- **Mock 모드 보호**: mock 함수 실패 시에도 크레딧 rollback 보장

### 인프라
- **Non-root 컨테이너**: appuser (uid=1000)로 실행, EC2 ec2-user와 UID 일치
- **볼륨 권한 자동 복구**: 배포 시 `sudo chown -R 1000:1000 ~/data`
- **Vertex AI location 검증**: 정규식으로 유효한 리전만 허용
- **로그인 브루트포스 방지**: rate limiting 적용

---

## 의존성

```
streamlit
requests
google-auth                      # Vertex AI / GCS 인증
PyJWT
streamlit-cookies-controller
pandas
```
