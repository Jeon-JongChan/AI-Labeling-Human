# 문서 라벨링 및 AI 데이터 가공 도구 (Document Labeling & AI Data Tool)

PDF 문서에서 텍스트, 표, 그림 요소를 추출하고, EasyOCR 및 VLM(비전-언어 모델)을 통해 검색·인제스트용 설명을 자동 생성 및 검수하는 독립형 웹 도구입니다.  
Standalone web tool for extracting text, tables, and images from PDFs, and generating/verifying search and ingestion captions using EasyOCR and Vision-Language Models (VLM).

---

## 1. 빠른 시작 (Quick Start)

Windows:
```bat
labeler.bat
```

Linux / macOS:
```bash
chmod +x labeler.sh && ./labeler.sh
```

- **웹 UI 접속 주소**: `http://localhost:8788`  
  Web UI URL: `http://localhost:8788`
- **가상환경 자동 구성**: `.venv` 생성 및 필요 의존성 자동 점검·설치.  
  Automatic virtual environment (`.venv`) setup and dependency verification.

---

## 2. 환경 설정 (.env)

`.env.example`을 복사하여 `.env` 생성 후 설정합니다.  
Copy `.env.example` to `.env` and configure variables.

```env
# 1) Google AI 모드 웹 크롤러 사용 시 (로그인 불필요, 추천)
VLM_PROVIDER=google
GOOGLE_CRAWLER_URL=http://127.0.0.1:8792

# 2) Google Gemini API 사용 시 (API 키 보유 시 권장, 내 PC 부하 없음)
# GEMINI_API_KEY=your_api_key_here
# VLM_PROVIDER=gemini

# 3) ChatGPT 웹 크롤러 서버 사용 시 (.tools/auto-query-web/gpt.py, 1회 로그인 필요)
# VLM_PROVIDER=gpt
# GPT_CRAWLER_URL=http://127.0.0.1:8791

# 4) Colab 원격 GPU 서버 사용 시 (.tools/colab/vlm_server_colab.ipynb)
# VLM_PROVIDER=colab
# VLM_BASE_URL=https://xxxx.trycloudflare.com/v1

# 문서 및 데이터 경로 (미지정 시 자동 감지)
# LABELER_DOCS_DIR=../../data/docs
# LABELER_DATA_DIR=../../data/labeling
```

### 주요 환경 변수 (Key Environment Variables)

| 변수명 (Variable) | 기본값 (Default) | 설명 (Description) |
| :--- | :--- | :--- |
| `VLM_PROVIDER` | `google` (`google`, `gemini`, `gpt`, `colab`, `local`, `auto`) | VLM 공급자 / VLM provider |
| `GOOGLE_CRAWLER_URL` | `http://127.0.0.1:8792` | Google AI 모드 웹 크롤러 엔드포인트 / Google AI mode crawler URL |
| `GPT_CRAWLER_URL` | `http://127.0.0.1:8791` | ChatGPT 웹 크롤러 엔드포인트 / ChatGPT crawler URL |
| `GEMINI_API_KEY` | (None) | Google Gemini API 키 / Google Gemini API key |
| `GEMINI_MODEL` | `gemini-1.5-flash` | Gemini 모델명 / Gemini model name |
| `VLM_BASE_URL` | `http://localhost:8090/v1` | 로컬 또는 Colab VLM 엔드포인트 / Local or Colab VLM endpoint |
| `LABELER_DOCS_DIR` | `data/docs` | PDF 원본 폴더 / Source PDF directory |
| `LABELER_DATA_DIR` | `data/labeling` | DB 및 크롭 이미지 저장 폴더 / Output DB & images directory |
| `LABELER_PORT` | `8788` | 웹 서버 포트 / Web server port |
| `OCR_GPU` | `true` | EasyOCR GPU 가속 여부 / Enable EasyOCR GPU acceleration |

---

## 3. VLM 비전 모델 옵션 (VLM Vision Model Options)

### 옵션 A: Google AI 모드 웹 크롤러 (가장 간편 / 로그인 불필요)
- 실행: `python .tools/auto-query-web/google.py`
- 계정 로그인 없이 Playwright Chromium을 통해 Google 검색 AI 모드로 질의합니다.
- 상세 옵션 및 구조는 [`.tools/auto-query-web/README.md`](file:///c:/Users/user/Documents/projects/local-rag/tools/AI-Labeling-Human/.tools/auto-query-web/README.md) 참조.

### 옵션 B: Google Gemini API (공식 API)
- `.env`에 `GEMINI_API_KEY` 입력, `VLM_PROVIDER=gemini`.
- 일일 1,500회 무료 호출 가능, 로컬 PC 하드웨어 부하 없음.

### 옵션 C: ChatGPT 웹 크롤러 (auto-query-web/gpt.py)
- 최초 1회 로그인: `python .tools/auto-query-web/gpt.py --login`
- 서버 기동: `python .tools/auto-query-web/gpt.py`
- 임시 채팅을 통해 ChatGPT 웹으로 이미지 캡션을 자동 생성합니다.

### 옵션 D: Colab 원격 GPU 연동 (Qwen2.5-VL-7B)
- `.tools/colab/vlm_server_colab.ipynb`를 Colab T4 GPU에서 실행.
- 생성된 Cloudflare 터널 URL을 `.env`의 `VLM_BASE_URL`에 입력, `VLM_PROVIDER=colab`.

### 옵션 E: 로컬 PC llama-server (Qwen2.5-VL-3B)
- `labeler.bat` 실행 시 안내에 따라 로컬 llama-server 설치 및 구동, `VLM_PROVIDER=local`.
- 기본 포트 `8090` 사용.

### 자동 순차 폴백 (Fallback Logic)
- `VLM_PROVIDER=auto` 설정 시: Colab 환경 우선 감지 -> Gemini API 호출 -> 웹 크롤러 확인 -> 한도 초과/미설정 시 로컬 Qwen2.5-VL로 자동 전환.

---

## 4. 디렉터리 구조 (Directory Structure)

```text
local-rag/
├── data/
│   ├── docs/          # 원본 PDF 저장소 / Source PDFs
│   └── labeling/      # SQLite DB (labeler.sqlite3) 및 크롭 이미지
└── tools/labeler/     # 라벨러 도구 루트 / Tool root
    ├── app/           # 핵심 애플리케이션 (서버, 추출기, VLM 어댑터)
    │   ├── doc_extract.py   # 문서 추출 단일 파사드
    │   ├── vlm.py           # VLM 캡셔닝 단일 파사드
    │   ├── extract/         # PDF 추출 어댑터/팩토리 패키지
    │   └── vlm_provider/    # VLM 공급자 어댑터 (gemini, google, gpt, colab, local)
    └── .tools/        # 보조 도구 (웹 크롤러, Colab, 테스트, 점검)
        ├── auto-query-web/  # Chromium 웹 질의 크롤러 (google.py, gpt.py)
        │   ├── google.py    # Google AI 모드 크롤러 (:8792, 로그인 불필요)
        │   ├── gpt.py       # ChatGPT 웹 크롤러 (:8791, 1회 로그인 필요)
        │   └── browsers/    # 공유 Playwright Chromium 바이너리
        ├── colab/           # Colab 노트북 및 가이드
        ├── test/            # 통합 테스트 스위트
        └── ensure.py        # GPU/Java/VLM 환경 자동 점검기
```

---

## 5. 전체 문서 일괄 처리 CLI (Batch CLI)

모든 PDF 파싱, OCR 및 VLM 캡셔닝을 터미널에서 일괄 실행합니다.  
Automated PDF extraction, OCR, and VLM captioning for all files via CLI.

```bash
python app/batch_process.py --vlm --provider google
```

- `--force`: 기존 추출 결과 무시하고 강제 재추출.
- `--no-ocr`: EasyOCR을 제외하고 구조 추출만 진행.
- `--provider {google,gemini,gpt,local,colab}`: VLM 공급자 명시 지정.
- `--doc <파일명>`: 특정 단일 문서만 지정 처리.

---

## 6. 내보내기 및 일괄 검수 (Export & Batch Update)

- **JSONL 적합만**: AI RAG 인제스트 및 학습용 데이터 (표·그림 Base64 이미지 포함).  
  JSONL (Approved only): For ingestion and training (includes Base64 images).
- **JSONL 전체**: `status` 일괄 수정용 경량 메타데이터 파일.  
  JSONL (All): Lightweight file for bulk `status` editing (`1`: 적합, `0`: 부적합, `null`: 미검토).
- **JSONL 올리기**: 수정한 JSONL을 웹 UI에서 업로드하여 일괄 반영.
