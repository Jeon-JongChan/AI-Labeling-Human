# Gemini API 설정 및 라벨러 실행 가이드

## 1. 핵심 요약
- **목적:** PDF 구조 파싱, EasyOCR, 표/다이어그램 설명(Gemini 1.5 Flash VLM) 자동 생성.
- **기본 방식:** 내 PC에서 Gemini API로 실행 (PC 부하 없음, 무료 티어 일일 1,500회 호출 지원).
- **자동 폴백:** Gemini API 일일 무료 한도(HTTP 429) 소진 시 로컬 Qwen2.5-VL 모델 등으로 자동 전환 지원.

---

## 2. Gemini API 키 발급 및 설정 (최초 1회)

1. **API 키 발급:**
   - [Google AI Studio](https://aistudio.google.com/)에 접속하여 Google 계정으로 로그인 후 API 키를 발급받습니다 (무료).

2. **`.env` 파일 설정:**
   - `tools/labeler/.env` 파일에 아래와 같이 입력합니다:
     ```env
     GEMINI_API_KEY=본인의_발급받은_API_키
     VLM_PROVIDER=gemini
     ```

---

## 3. VLM 동작 우선순위 (자동 전환)
1. **Google Colab AI / Secrets:** 코랩 환경일 때 우선 감지.
2. **Google Gemini 1.5 Flash:** 일반 환경 기본값 (하루 1,500회 무료, 내 PC 부하 0%).
3. **로컬 Qwen2.5-VL-3B / 기타:** 위 API 한도 초과(HTTP 429) 시 자동으로 전환되어 계속 실행.

---

## 4. 실행 방법

### A. 내 PC에서 전체 문서 일괄 처리 (CLI)
라벨링할 PDF를 `data/docs/`에 넣고 `tools/labeler` 디렉터리에서 실행:
```bash
python app/batch_process.py --vlm
```
- 모든 문서를 순회하며 텍스트 파싱 + OCR + 다이어그램 설명까지 자동 완료.
- 이미 추출된 문서는 자동 스킵.
- 강제 재추출: `--force`
- 순수 텍스트만 추출 (OCR 제외): `--no-ocr`
- 공급자 강제 지정: `--provider gemini` 또는 `--provider gpt` / `--provider local`

### B. 웹 UI로 확인 및 라벨링
`labeler.bat` (윈도우) 또는 `labeler.sh` (리눅스) 더블클릭:
- 브라우저 `http://localhost:8788` 자동 오픈.
- `.env`에 키가 있으면 로컬 모델 설치 질문 없이 즉시 실행.

---

## 5. 입출력 경로
- **입력 PDF 폴더:** `data/docs/`
- **라벨 DB:** `data/labeling/labeler.sqlite3`
- **크롭 이미지:** `data/labeling/images/`
*(경로 변경 필요 시 `.env`의 `LABELER_DOCS_DIR`, `LABELER_DATA_DIR` 수정)*
