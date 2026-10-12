"""
Qwen2.5-VL-3B-Instruct 경량 서빙 서버 (FastAPI / OpenAI 호환).

Colab GPU 환경(또는 로컬 CUDA)에서 Qwen2.5-VL을 포트 8090에 서빙하여
labeler의 웹 UI 및 batch_process.py와 연동합니다.

실행:
  python tools/labeler/vlm_server.py
"""

from __future__ import annotations

import base64
import io
import os
import sys
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parent
if str(TOOL_DIR) not in sys.path:
    sys.path.insert(0, str(TOOL_DIR))

from config import VLM_PORT  # noqa: E402


def run_server() -> None:
    import torch
    import uvicorn
    from fastapi import FastAPI, Request
    from PIL import Image
    from transformers import AutoProcessor, Qwen2_5_VLForConditionalGeneration

    model_id = os.getenv("VLM_MODEL_ID", "Qwen/Qwen2.5-VL-3B-Instruct")
    print(f"[vlm_server] 모델 로딩 중: {model_id} (CUDA={torch.cuda.is_available()}) ...", flush=True)

    device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype = torch.float16 if torch.cuda.is_available() else torch.float32

    processor = AutoProcessor.from_pretrained(model_id)
    model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
        model_id,
        torch_dtype=dtype,
        device_map="auto" if torch.cuda.is_available() else None,
    )
    if not torch.cuda.is_available():
        model = model.to(device)

    app = FastAPI(title="Qwen2.5-VL OpenAI Compatible Server")

    @app.get("/health")
    @app.get("/v1/models")
    async def health():
        return {"status": "ok", "model": model_id}

    @app.post("/v1/chat/completions")
    async def chat_completions(req: Request):
        payload = await req.json()
        messages = payload.get("messages", [])
        max_tokens = payload.get("max_tokens", 1024)
        temperature = payload.get("temperature", 0.2)

        # 메시지에서 텍스트와 이미지 추출
        prompt_text = ""
        images: list[Image.Image] = []

        for msg in messages:
            content = msg.get("content")
            if isinstance(content, str):
                prompt_text += content + "\n"
            elif isinstance(content, list):
                for part in content:
                    ptype = part.get("type")
                    if ptype == "text":
                        prompt_text += part.get("text", "") + "\n"
                    elif ptype == "image_url":
                        url = part.get("image_url", {}).get("url", "")
                        if url.startswith("data:image/") and ";base64," in url:
                            b64_data = url.split(";base64,")[1]
                            img_bytes = base64.b64decode(b64_data)
                            images.append(Image.open(io.BytesIO(img_bytes)).convert("RGB"))

        # transformers 형식 메시지 생성
        formatted_content = []
        for img in images:
            formatted_content.append({"type": "image", "image": img})
        formatted_content.append({"type": "text", "text": prompt_text.strip()})

        conv = [{"role": "user", "content": formatted_content}]
        text_prompt = processor.apply_chat_template(conv, tokenize=False, add_generation_prompt=True)

        inputs = processor(
            text=[text_prompt],
            images=images if images else None,
            padding=True,
            return_tensors="pt",
        )
        inputs = inputs.to(device)

        with torch.no_grad():
            outputs = model.generate(
                **inputs,
                max_new_tokens=max_tokens,
                temperature=temperature if temperature > 0 else None,
                do_sample=temperature > 0,
            )

        trimmed = [
            out[len(inp):] for inp, out in zip(inputs.input_ids, outputs)
        ]
        response_text = processor.batch_decode(trimmed, skip_special_tokens=True, clean_up_tokenization_spaces=False)[0]

        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": response_text.strip(),
                    }
                }
            ]
        }

    port = int(os.getenv("VLM_PORT", str(VLM_PORT)))
    print(f"[vlm_server] VLM 서버 준비 완료! http://127.0.0.1:{port}", flush=True)
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")


if __name__ == "__main__":
    run_server()

