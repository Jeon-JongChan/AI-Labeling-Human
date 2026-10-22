# gpt-auto-query — ChatGPT 웹 기반 VLM 캡셔닝 서버

Playwright Chromium을 활용하여 ChatGPT 웹 화면을 통해 이미지를 분석하고 설명을 생성하는 자동화 로컬 프록시 서버입니다.

> [!WARNING]
> 웹 자동화(스크래핑/크롤링)는 서비스 이용약관 위반 소지가 있으므로 **반드시 보조/테스트용 계정**으로 사용하시기 바랍니다.

---

## 1. 주요 특징

- **포터블 독립 환경**:
  - `browsers/`: 전용 Chromium 브라우저 바이너리를 보관하여 시스템 환경에 영향을 주지 않습니다.
  - `profile/`: 사용자 로그인 세션을 로컬에 영구 보존하므로 매번 로그인할 필요가 없습니다.
- **임시 채팅(Temporary Chat) 자동 활용**:
  - `https://chatgpt.com/?temporary-chat=true`로 접속하여 이전 대화 기록이 남거나 히스토리가 오염되지 않습니다.
- **계정 보호 및 안전 중단(Safety Abort / Circuit Breaker)**:
  - **401 Unauthorized**: 로그인 세션 만료 즉시 감지 및 안전 중단.
  - **423 Locked**: 연속 3회 실패 시 계정 보호를 위해 서버 차단 (무한 루프 방지).
  - 요청 간 최소 딜레이(기본 3초)를 두어 비정상 트래픽 탐지 완화.

---

## 2. 빠른 시작 (최초 1회 설정)

`tools/labeler` 폴더에서 진행합니다.

### 1단계: 브라우저 설치 (최초 1회)
```bash
python .tools/gpt-auto-query/server.py --install
```
> Chromium 바이너리를 `.tools/gpt-auto-query/browsers/` 폴더 내에 로컬 다운로드합니다.

### 2단계: ChatGPT 로그인 (최초 1회)
```bash
python .tools/gpt-auto-query/server.py --login
```
1. 크롬 브라우저 창이 열리면 사용할 계정으로 로그인합니다.
2. 로그인이 완료되어 채팅창이 정상 표시되면, 터미널로 돌아와 **Enter** 키를 누릅니다.
3. 세션 쿠키가 `profile/` 디렉터리에 안전하게 저장됩니다.

---

## 3. 서버 실행 및 라벨러 연동

### 1단계: 크롤러 서버 기동
```bash
python .tools/gpt-auto-query/server.py
```
- 기본 주소: `http://127.0.0.1:8791`
- 브라우저 창이 표시되며 백그라운드 대기 상태로 진입합니다.

### 2단계: 라벨러 `.env` 설정
`tools/labeler/.env` 파일에 아래 설정을 적용합니다:
```env
VLM_PROVIDER=gpt
GPT_CRAWLER_URL=http://127.0.0.1:8791
```

### 3단계: 일괄 분석 또는 라벨러 실행
- **CLI 일괄 분석 실행:**
  ```bash
  python app/batch_process.py --vlm --provider gpt
  ```
- **웹 UI 라벨러 실행:**
  ```bash
  labeler.bat
  ```

---

## 4. 환경 변수 및 상세 옵션

`.env` 또는 실행 환경 변수를 통해 동작 방식을 커스텀할 수 있습니다:

| 환경 변수 | 기본값 | 설명 |
|---|---|---|
| `GPT_CRAWLER_HOST` | `127.0.0.1` | 바인딩 호스트 |
| `GPT_CRAWLER_PORT` | `8791` | 바인딩 포트 |
| `GPT_HEADLESS` | `0` (창 표시) | `1`로 설정 시 백그라운드 무창 모드 (봇 탐지 회피를 위해 0 권장) |
| `GPT_RESPONSE_TIMEOUT`| `180` | ChatGPT 답변 대기 최대 시간(초) |
| `GPT_MIN_INTERVAL` | `3.0` | 이미지 요청 간 최소 휴식 시간(초) |
| `GPT_MAX_FAILS` | `3` | 연속 실패 시 차단(423 Locked) 임계값 |

---

## 5. API 명세

| 엔드포인트 | 메서드 | 요청 바디 | 응답 | 설명 |
|---|---|---|---|---|
| `/health` | `GET` | 없음 | `{"ok": true, "busy": false}` | 서버 상태 및 작업 진행 여부 점검 |
| `/caption` | `POST` | `{"image_b64": "...", "prompt": "..."}` | `{"text": "..."}` | 이미지 전송 및 캡션 획득 |

- 상태 코드:
  - `200 OK`: 정상 응답 반환
  - `401 Unauthorized`: 로그인 필요/세션 만료
  - `423 Locked`: 연속 에러로 안전 중단(서버 재시작 필요)
  - `500 Internal Server Error`: 파싱 또는 셀렉터 에러

