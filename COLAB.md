# 라벨러 실행 가이드 (Colab & 로컬)

## 1. 핵심 요약
- **목적:** PDF 구조 파싱, EasyOCR, 표/다이어그램 설명 자동 생성.
- **기본 방식:** 내 PC에서 Gemini API로 실행 (PC 부하 없음, 코랩 필요 없음).
- **Colab/로컬 모델 용도:** Gemini 일일 무료 한도(1,500회) 소진 시 백업.

---

## 2. VLM 동작 우선순위 (자동 전환)
1. **Google Colab AI / Secrets:** 코랩 환경일 때 우선 감지.
2. **Google Gemini 1.5 Flash:** 일반 환경 기본값 (하루 1,500회 무료, 내 PC 부하 0%).
3. **로컬 Qwen2.5-VL-3B:** 위 API 한도 초과(HTTP 429) 시 자동으로 전환되어 계속 실행.

---

## 3. 설정 (최초 1회)
`tools/labeler/.env` 파일 생성 후 키 입력:
```env
GEMINI_API_KEY=본인의_API_키
```
*(키 발급: https://aistudio.google.com/)*

---

## 4. 실행 방법

### A. 내 PC에서 전체 문서 일괄 처리 (가장 권장)
라벨링할 PDF를 `data/docs/`에 넣고 터미널에서 실행:
```bash
python tools/labeler/batch_process.py --vlm
```
- 모든 문서를 순회하며 텍스트 파싱 + OCR + 다이어그램 설명까지 자동 완료.
- 이미 추출된 문서는 자동 스킵.
- 강제 재추출: `--force`
- 순수 텍스트만 추출 (OCR 제외): `--no-ocr`
- 공급자 강제 지정: `--provider gemini` 또는 `--provider local`

### B. 웹 UI로 확인 및 라벨링
`tools/labeler/labeler.bat` (윈도우) 또는 `labeler.sh` (리눅스) 더블클릭:
- 브라우저 `http://localhost:8788` 자동 오픈.
- `.env`에 키가 있으면 로컬 모델 설치 질문 없이 1초 만에 실행.

### C. Google Colab에서 실행 (대량 작업 / 비상용)
1. Google Drive의 `MyDrive/local-rag`에 필수 파일 업로드 (`.venv`, `models` 등 무거운 폴더 제외).
2. Colab에서 `labeler_colab.ipynb` 열고 T4 GPU 연결.
3. 1~3번 셀 실행 후 바로 5번 셀(`batch_process.py --vlm`) 실행.
4. 작업 완료 후 7번 셀로 Google Drive에 백업.

---

## 5. 입출력 경로
- **입력 PDF 폴더:** `data/docs/`
- **라벨 DB:** `data/labeling/labeler.sqlite3`
- **크롭 이미지:** `data/labeling/images/`
*(경로 변경 필요 시 `.env`의 `LABELER_DOCS_DIR`, `LABELER_DATA_DIR` 수정)*

