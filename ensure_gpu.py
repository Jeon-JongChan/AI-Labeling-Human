"""
GPU(CUDA) 환경 점검 및 자동 설치 스크립트.

시스템에 NVIDIA GPU가 감지되었으나 PyTorch가 CPU 전용으로 설치되어 있는 경우,
CUDA 가속 휠(cu124)을 자동으로 설치하여 다른 컴퓨터에서도 별도 설정 없이 GPU가 우선 동작하도록 보장합니다.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


def check_nvidia_smi() -> bool:
    """nvidia-smi 실행 가능 여부로 물리적 NVIDIA GPU 장착을 확인합니다."""
    nvsmi = shutil.which("nvidia-smi")
    if not nvsmi:
        return False
    try:
        res = subprocess.run([nvsmi], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return res.returncode == 0
    except Exception:
        return False


def ensure_gpu_torch() -> bool:
    """
    NVIDIA GPU가 존재하고 CUDA PyTorch가 미설치된 경우 자동으로 cu124를 설치합니다.
    """
    has_gpu = check_nvidia_smi()
    if not has_gpu:
        print("[labeler] NVIDIA GPU 미감지 - CPU 모드로 동작합니다.", flush=True)
        return False

    try:
        import torch
        if torch.cuda.is_available():
            dev_name = torch.cuda.get_device_name(0)
            print(f"[labeler] GPU 가속 활성화됨: {dev_name} (CUDA {torch.version.cuda})", flush=True)
            return True
        print("[labeler] NVIDIA GPU가 감지되었으나 현재 PyTorch가 CPU 전용입니다.", flush=True)
    except ImportError:
        print("[labeler] PyTorch 미설치 감지. GPU 버전 설치를 진행합니다.", flush=True)

    # CUDA 12.4 지원 PyTorch 자동 설치
    print("[labeler] CUDA 가속 PyTorch(cu124) 자동 설치를 진행합니다...", flush=True)
    cmd = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--force-reinstall",
        "torch",
        "torchvision",
        "--index-url",
        "https://download.pytorch.org/whl/cu124",
    ]
    try:
        res = subprocess.run(cmd, check=True)
        if res.returncode == 0:
            print("[labeler] CUDA PyTorch 설치 완료!", flush=True)
            return True
    except Exception as exc:
        print(f"[labeler] [경고] CUDA PyTorch 설치 실패 ({exc}) - CPU 모드로 계속합니다.", flush=True)
    return False


if __name__ == "__main__":
    ensure_gpu_torch()

