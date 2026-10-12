# AI Labeling Human

PDF 텍스트·표·그림 추출 및 사람 검증용 라벨링 도구.  
Web tool for human verification and labeling of PDF text, tables, and figures.

---

## 1. 빠른 시작 (Quick Start)

### 실행 (Run)
Windows:
```bat
labeler.bat
```
Linux / macOS:
```bash
chmod +x labeler.sh && ./labeler.sh
```
- 브라우저 접속 주소: `http://localhost:8788`  
  Web UI URL: `http://localhost:8788`
- 가상환경(`.venv`) 및 필요 패키지 자동 설치.  
  Automatic virtual environment (`.venv`) setup and dependency installation.

---

## 2. 환경 설정 (.env)

`.env.example`을 복사하여 `.env` 생성 후 설정.  
Copy `.env.example` to `.env` and configure variables.

```env
# Gemini API 사용 시 (권장, 내 PC 부하 없음)
# For Gemini API (Recommended, no PC workload)
GEMINI_API_KEY=your_api_key_here

# Colab 원격 GPU 서버 사용 시 (colab/vlm_server_colab.ipynb)
# For Colab remote GPU server (colab/vlm_server_colab.ipynb)
VLM_PROVIDER=local
VLM_BASE_URL=https://xxxx.trycloudflare.com/v1

# 문서 및 데이터 경로 (미지정 시 자동 감지)
# Document and data paths (Auto-detected if omitted)
# LABELER_DOCS_DIR=../../data/docs
# LABELER_DATA_DIR=../../data/labeling
```

### 주요 환경 변수 (Key Environment Variables)

| 변수명 (Variable) | 기본값 (Default) | 설명 (Description) |
| :--- | :--- | :--- |
| `GEMINI_API_KEY` | (None) | Google Gemini API 키 / Google Gemini API key |
| `GEMINI_MODEL` | `gemini-3.8-flash` | Gemini 모델명 / Gemini model name |
| `VLM_PROVIDER` | `gemini` (키 있을 때) / `local` | VLM 공급자 (`gemini`, `local`) / VLM provider (`gemini`, `local`) |
| `VLM_BASE_URL` | `http://localhost:8090/v1` | 로컬 또는 Colab VLM 엔드포인트 / Local or Colab VLM endpoint |
| `LABELER_DOCS_DIR` | `data/docs` | PDF 원본 폴더 / Source PDF directory |
| `LABELER_DATA_DIR` | `data/labeling` | DB 및 이미지 저장 폴더 / Output DB & images directory |
| `LABELER_PORT` | `8788` | 웹 서버 포트 / Web server port |
| `OCR_GPU` | `true` | EasyOCR GPU 가속 여부 / Enable EasyOCR GPU acceleration |

---

## 3. VLM 비전 모델 옵션 (VLM Vision Model Options)

### 옵션 A: Google Gemini API (가장 권장)
### Option A: Google Gemini API (Recommended)
- `.env`에 `GEMINI_API_KEY` 입력.  
  Set `GEMINI_API_KEY` in `.env`.
- 일일 1,500회 무료 호출 가능, 로컬 PC 부하 없음.  
  Free 1,500 requests/day, zero local hardware load.

### 옵션 B: Colab 원격 GPU 연동 (Qwen2.5-VL-7B)
### Option B: Colab Remote GPU (Qwen2.5-VL-7B)
- `colab/vlm_server_colab.ipynb`를 Colab T4 GPU에서 실행.  
  Run `colab/vlm_server_colab.ipynb` on a Colab T4 GPU instance.
- 출력된 Cloudflare 터널 URL을 `.env`의 `VLM_BASE_URL`에 입력.  
  Set the generated Cloudflare tunnel URL to `VLM_BASE_URL` in `.env`.
- `VLM_PROVIDER=local` 설정.  
  Set `VLM_PROVIDER=local`.

### 옵션 C: 로컬 PC llama-server (Qwen2.5-VL-3B)
### Option C: Local PC llama-server (Qwen2.5-VL-3B)
- `labeler.bat` 실행 시 안내에 따라 설치 및 구동.  
  Installed and started automatically via `labeler.bat` prompts.
- 기본 포트 `8090` 사용.  
  Runs on default port `8090`.

### 자동 전환 로직 (Fallback Logic)
- Gemini 일일 쿼터 초과(HTTP 429) 시 자동으로 Colab/로컬 모델로 전환.  
  Automatically falls back to Colab/local VLM upon Gemini quota exhaustion (HTTP 429).

---

## 4. 경로 및 프로젝트 루트 (Paths & Project Root)

`local-rag` 내부에 위치할 경우 상위 디렉터리를 자동 인식.  
Automatically recognizes parent directories when located inside `local-rag`.

```text
local-rag/
├── data/
│   ├── docs/          # 입력 PDF 저장소 / Input PDFs
│   └── labeling/      # SQLite DB 및 크롭 이미지 / DB & crop images
└── tools/
    └── AI-Labeling-Human/  # 라벨러 도구 루트 / Tool root
```

- 상위 `data/` 폴더 자동 감지 지원.  
  Supports automatic detection of parent `data/` directory.
- 독립 배포 실행 시 자체 폴더 내 `./data/` 사용.  
  Uses internal `./data/` directory during standalone deployment.

---

## 5. 전체 문서 일괄 처리 CLI (Batch CLI)

모든 PDF 파싱, OCR 및 VLM 캡셔닝 자동 실행.  
Automated PDF extraction, OCR, and VLM captioning for all files.

```bash
python batch_process.py --vlm
```

- `--force`: 기존 추출 결과 무시하고 강제 재추출.  
  Force re-extraction of already processed documents.
- `--no-ocr`: OCR 제외하고 텍스트만 추출.  
  Extract text only, skipping OCR.
- `--provider [gemini|local]`: VLM 공급자 강제 지정.  
  Force specific VLM provider.

---

## 6. 라벨링 작업 및 데이터 내보내기 (Labeling & Export)

### 데이터 형식 (Data Format)
- SQLite DB: `{LABEL_DIR}/labeler.sqlite3`  
  SQLite DB: `{LABEL_DIR}/labeler.sqlite3`
- 크롭 이미지: `{LABEL_DIR}/images/<doc_id>/`  
  Cropped images: `{LABEL_DIR}/images/<doc_id>/`

### 라벨 상태 값 (Label Status)
- `1`: 적합 (Approved)
- `0`: 부적합 (Rejected)
- `null`: 미검토 (Pending)

### 내보내기 및 일괄 수정 (Export & Batch Update)
- JSONL 적합만: 인제스트/학습용 데이터 (이미지 Base64 포함).  
  JSONL (Approved only): For ingestion and training (includes Base64 images).
- JSONL 전체: `status` 일괄 수정용 경량 파일.  
  JSONL (All): Lightweight file for bulk `status` editing.
- 수정된 JSONL 업로드 시 변경 사항 일괄 반영.  
  Upload edited JSONL to apply label changes in bulk.
