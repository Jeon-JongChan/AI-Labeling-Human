# auto-query-web — 웹 브라우저 기반 VLM 자동 질의 프록시 서버

Playwright Chromium을 활용하여 브라우저 화면에서 이미지를 분석하고 설명을 생성하는 자동화 로컬 프록시 서버 모음입니다.
Chromium 브라우저 바이너리(`browsers/`)를 공유하며, 두 가지 웹 모델(Google AI 모드, ChatGPT)을 지원합니다.

> [!WARNING]
> 웹 자동화(스크래핑/크롤링)는 서비스 이용약관 위반 소지가 있으므로 **반드시 보조/테스트용 계정**으로 사용하시기 바랍니다.

---

## 1. 지원 모드 및 빠른 비교

| 구분 | Google AI 모드 ([`google.py`](file:///c:/Users/user/Documents/projects/local-rag/tools/AI-Labeling-Human/.tools/auto-query-web/google.py)) | ChatGPT 웹 ([`gpt.py`](file:///c:/Users/user/Documents/projects/local-rag/tools/AI-Labeling-Human/.tools/auto-query-web/gpt.py)) |
| :--- | :--- | :--- |
| **로그인 필요 여부** | **불필요 (즉시 실행 가능)** | 필수 (최초 1회 수동 로그인) |
| **기본 포트** | `http://127.0.0.1:8792` | `http://127.0.0.1:8791` |
| **`.env` 설정** | `VLM_PROVIDER=google` | `VLM_PROVIDER=gpt` |
| **사용자 프로필** | `profile-google/` | `profile/` |
| **특징 및 한도** | 무료 계정 첨부 한도 없음 (대량 연속 질의 시 캡차 주의) | 무료 계정 시 주기적 파일 첨부 한도 제한 적용 |

---

## 2. Google AI 모드 실행 방법 (추천 / 로그인 불필요)

Google 검색의 [AI 모드](https://www.google.com/search?udm=50&hl=ko)를 조작하여 이미지를 분석합니다. 계정 로그인이 필요 없습니다.

### 1단계: 크롤러 서버 기동
터미널에서 아래 명령을 실행합니다:
```bash
python .tools/auto-query-web/google.py
```
- 브라우저 창이 뜨고 `http://127.0.0.1:8792`에서 요청 대기 상태가 됩니다.
- Playwright 및 Chromium이 미설치 상태인 경우 첫 실행 시 자동으로 설치됩니다.

### 2단계: 라벨러 `.env` 설정
`.env` 파일에 공급자를 `google`로 지정합니다:
```env
VLM_PROVIDER=google
GOOGLE_CRAWLER_URL=http://127.0.0.1:8792
```

### 3단계: 일괄 작업 또는 라벨러 시작
- **CLI 일괄 분석 실행:**
  ```bash
  python app/batch_process.py --vlm --provider google
  ```
- **라벨러 웹 UI 기동:**
  ```bat
  labeler.bat
  ```

---

## 3. ChatGPT 웹 실행 방법 (수동 로그인 1회 필요)

ChatGPT 임시 채팅(`https://chatgpt.com/?temporary-chat=true`)을 통해 이미지를 분석합니다.

### 1단계: 최초 1회 로그인 (세션 저장)
```bash
python .tools/auto-query-web/gpt.py --login
```
1. Chromium 창이 뜨면 사용할 ChatGPT 계정으로 로그인합니다.
2. 로그인이 완료되면 터미널로 돌아와 **Enter** 키를 누릅니다. 세션이 `profile/`에 영구 보존됩니다.

### 2단계: 크롤러 서버 기동
```bash
python .tools/auto-query-web/gpt.py
```
- 기본 주소: `http://127.0.0.1:8791`

### 3단계: 라벨러 `.env` 설정
```env
VLM_PROVIDER=gpt
GPT_CRAWLER_URL=http://127.0.0.1:8791
```

### 4단계: 실행
- **CLI 일괄 분석 실행:**
  ```bash
  python app/batch_process.py --vlm --provider gpt
  ```

---

## 4. 환경 변수 및 세부 옵션

`.env` 또는 터미널 환경 변수를 통해 동작 방식을 설정할 수 있습니다:

| 환경 변수 | 기본값 | 대상 | 설명 |
|---|---|---|---|
| `WEB_QUERY_MIN_INTERVAL` | `3.0` (대량 20) | 웹 공통 | 요청 간 최소 휴식 시간(초, 비정상 트래픽/봇 탐지 완화) |
| `WEB_QUERY_HEADLESS` | `0` (창 표시) | 웹 공통 | `1` 설정 시 무창 모드 (탐지 회피를 위해 0 창 표시 권장) |
| `WEB_QUERY_TIMEOUT` | `180` | 웹 공통 | 웹 모델 답변 완료 대기 타임아웃(초) |
| `WEB_QUERY_MAX_FAILS` | `3` | 웹 공통 | 연속 실패 시 안전 중단(423 Locked) 임계값 |
| `GOOGLE_CRAWLER_URL` | `http://127.0.0.1:8792` | 라벨러 앱 | Google AI 모드 크롤러 접속 주소 |
| `GOOGLE_CRAWLER_PORT` | `8792` | Google | Google 크롤러 바인딩 포트 |
| `GPT_CRAWLER_URL` | `http://127.0.0.1:8791` | 라벨러 앱 | ChatGPT 크롤러 접속 주소 |
| `GPT_CRAWLER_PORT` | `8791` | ChatGPT | ChatGPT 크롤러 바인딩 포트 |
| *(기존 `GPT_*` 변수)* | - | 하위 호환 | 기존 `GPT_MIN_INTERVAL` 등도 그대로 동작 보장 |

---

## 5. API 규격 (공통)

두 서버(`google.py`, `gpt.py`) 모두 동일한 REST API 규격을 제공합니다:

| 엔드포인트 | 메서드 | 요청 바디 | 응답 | 설명 |
|---|---|---|---|---|
| `/health` | `GET` | 없음 | `{"ok": true, "fails": 0}` | 서버 헬스체크 및 연속 실패 횟수 점검 |
| `/reset` | `GET` | 없음 | `{"ok": true, "fails": 0}` | 연속 실패 카운터 수동 초기화 |
| `/caption` | `POST` | `{"image_b64": "...", "prompt": "..."}` | `{"text": "..."}` | 이미지 전송 및 설명 텍스트 획득 |

- 주요 HTTP 상태 코드:
  - `200 OK`: 정상 응답 반환
  - `400 Bad Request`: 요청 파라미터 누락 또는 Base64 디코딩 실패
  - `401 Unauthorized`: 로그인 필요 또는 세션 만료
  - `423 Locked`: 연속 실패 또는 서비스 한도(파일 첨부 한도 / 캡차) 도달로 차단 (배치 안전 중단)
  - `502 Bad Gateway`: 이미지 미첨부, DOM 썸네일 미생성, 빈 응답
