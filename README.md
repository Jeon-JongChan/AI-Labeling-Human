# local-rag 라벨링 도구

인제스트 전에 PDF에서 추출된 데이터(텍스트·표·그림)를 사람이 직접 확인하고
적합/부적합을 라벨링하는 웹 도구입니다. local-rag와 함께 쓰거나, 경로만 바꿔 독립 실행할 수 있습니다.

## 실행

```bat
scripts\labeler.bat
```

```bash
./scripts/labeler.sh
```

첫 실행 시 추가 의존성(pypdfium2, pillow, easyocr)을 자동 설치하고
`http://localhost:8788` 이 열립니다. (메인 앱 8787과 독립적으로 동작)

## 경로 설정 (독립 사용)

`tools/labeler/config.py` 에서 PDF·작업 폴더를 지정합니다.

```python
DOCS_DIR = Path(r"D:\my-pdfs")           # PDF 폴더
LABEL_DIR = Path(r"D:\labeling-out")     # DB·크롭 이미지 저장
HOST = "0.0.0.0"
PORT = 8788
```

`None` 이면 local-rag 기본(`data/docs`, `data/labeling`)을 씁니다.

환경 변수로도 지정 가능합니다 (**환경 변수 > config.py 직접 설정 > 기본값**).

| 변수 | 기본값 (local-rag 기준) | 설명 |
|------|-------------------------|------|
| `LABELER_DOCS_DIR` | `data/docs` | PDF 폴더 |
| `LABELER_DATA_DIR` | `data/labeling` | 라벨 DB·이미지 루트 |
| `LABELER_HOST` | `0.0.0.0` | 바인드 주소 |
| `LABELER_PORT` | `8788` | 포트 |

예 (Windows):

```bat
set LABELER_DOCS_DIR=D:\pdfs
set LABELER_DATA_DIR=D:\labeling-out
scripts\labeler.bat
```

예 (Linux):

```bash
export LABELER_DOCS_DIR=/data/pdfs
export LABELER_DATA_DIR=/data/labeling
./scripts/labeler.sh
```

시작 로그에 `DOCS_DIR` / `LABEL_DIR` 이 출력되니 경로가 맞는지 확인하세요.

## 사용 흐름

1. `DOCS_DIR` 에 PDF를 넣고 좌측 [docs 재탐색]
2. PDF 선택 → [추출]
   - 텍스트: OpenDataLoader 구조 추출
   - 표·그림: 해당 영역을 PNG로 크롭 (pypdfium2)
   - 그림: EasyOCR(ko+en)로 글자 추출 (OCR 체크박스로 켜고 끔, 첫 실행 시 모델 다운로드)
3. 항목별로 확인
   - 텍스트 수정 가능 (오인식 교정) — 수정 시 자동 저장
   - [✓ 적합] / [✗ 부적합] 클릭 (다시 누르면 미검토로 복귀)
   - 필터: 미검토/적합/부적합 · 텍스트/표/그림
   - 그림·표 카드의 [VLM 설명 생성] — VL 모델이 이미지 자체를 이해해
     설명(다이어그램 연결 관계 등)을 만들어 텍스트란에 채웁니다 (아래 참고)
4. 내려받기
   - **JSONL 적합만** — 이후 인제스트에 쓸 정제 데이터
   - **JSONL 전체** — status 포함 전체 기록
   - **HTML** — 사람 확인용 (크롭 이미지 base64 내장, 단일 파일)

## 저장 위치

| 경로 | 내용 |
|------|------|
| `{LABEL_DIR}/labeler.sqlite3` | 문서·항목·라벨 |
| `{LABEL_DIR}/images/<doc_id>/` | 표·그림 크롭 PNG |

## JSONL 형식 (1줄 = 1항목)

```json
{"file_name": "a.pdf", "rel_path": "a.pdf", "page": 3,
 "bbox": [x0, y0, x1, y1], "element_type": "table", "kind": "table",
 "text": "최종 텍스트(수정본 우선)", "ocr_text": null,
 "image_path": "1/00012_table.png", "status": "approved"}
```

## VLM 설명 생성 (선택 기능)

OCR로 잡히지 않는 다이어그램(서버 간 연결선 등)을 VL(비전-언어) 모델이 읽고
검색용 한국어 설명을 생성합니다. 별도의 VL llama-server가 필요합니다.

```bat
rem 모델 + mmproj 자동 다운로드 (Qwen2.5-VL-3B, 약 3GB)
llama\llama-server.exe -hf ggml-org/Qwen2.5-VL-3B-Instruct-GGUF --port 8090
```

서버가 뜬 뒤 그림/표 카드에서 [VLM 설명 생성]을 누르면 결과가 텍스트란에
채워지고 자동 저장됩니다. 마음에 안 들면 직접 고치거나 되돌리면 됩니다.

환경 변수로 조정 가능:

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `VLM_BASE_URL` | `http://localhost:8090/v1` | VL 서버 주소 |
| `VLM_MODEL` | (빈값) | 모델 alias (보통 불필요) |
| `VLM_MAX_TOKENS` | `1024` | 설명 최대 길이 |
| `VLM_TIMEOUT` | `300` | 응답 대기 초 |

CPU(4800U)에서는 그림 1장에 수십 초~수 분 걸릴 수 있습니다.
대량 처리라면 Colab 등 GPU 환경에서 돌리는 편이 낫습니다.

"다시 추출"을 누르면 해당 문서의 기존 라벨이 삭제되니 주의하세요.
인제스트 연동(승인 항목만 벡터DB에 넣기)은 추후 작업 예정입니다.
