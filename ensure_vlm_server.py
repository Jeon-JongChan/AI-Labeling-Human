"""
라벨러용 VL(멀티모달) llama-server 자동 준비 (이 폴더만으로 독립 동작).

1) config.VLM_PORT 가 응답하면 종료
2) ./llama-cpp 에 llama-server 없으면 GitHub 릴리스에서 설치
3) llama-server -hf <VLM_HF_REPO> --port <VLM_PORT> 기동 후 헬스체크
   - Windows: 새 콘솔 / Linux: 백그라운드 + llama-cpp/llama-vlm-server.log
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

TOOL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOL_DIR))

from config import VLM_BASE_URL, VLM_HF_REPO, VLM_PORT, vlm_server_cmd_hint  # noqa: E402

LLAMA_DIR = TOOL_DIR / "llama-cpp"
META_PATH = LLAMA_DIR / ".install_meta.json"
USER_AGENT = "ai-labeling-human-ensure-vlm/1.0"
GITHUB_API = "https://api.github.com/repos/ggml-org/llama.cpp/releases/{tag}"


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _parse_host_port() -> tuple[str, int]:
    parsed = urlparse(VLM_BASE_URL.rstrip("/") + "/")
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or VLM_PORT
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"
    return host, port


def _port_listening(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _http_ok(url: str, timeout: float = 3.0) -> bool:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def server_healthy(host: str, port: int) -> bool:
    if not _port_listening(host, port):
        return False
    for path in ("/health", "/v1/models", "/"):
        if _http_ok(f"http://{host}:{port}{path}"):
            return True
    return False


def _download(url: str, dest: Path, label: str) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    print(f"[vlm] 다운로드: {label}")
    print(f"      {url}")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        chunk = 1024 * 1024
        with tmp.open("wb") as f:
            while True:
                buf = resp.read(chunk)
                if not buf:
                    break
                f.write(buf)
                done += len(buf)
                if total > 0 and done % (10 * chunk) < chunk:
                    pct = done * 100 // total
                    print(f"      ... {done // (1024 * 1024)} MB ({pct}%)")
    tmp.replace(dest)
    print(f"[vlm] 저장: {dest}")


def _fetch_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _cpu_arch() -> str:
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        return "x64"
    if machine in ("aarch64", "arm64"):
        return "arm64"
    return machine


def _detect_variant() -> str:
    explicit = _env("LLAMA_CPP_VARIANT").lower()
    if explicit in ("cpu", "vulkan", "cuda"):
        return explicit
    try:
        r = subprocess.run(
            ["nvidia-smi"],
            capture_output=True,
            timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        if r.returncode == 0:
            return "cuda" if sys.platform == "win32" else "vulkan"
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return "vulkan" if sys.platform == "win32" else "cpu"


def _pick_asset_url(release: dict, variant: str) -> Optional[str]:
    assets = release.get("assets") or []
    arch = _cpu_arch()

    def find_win(substr: str) -> Optional[str]:
        for a in assets:
            n = a.get("name", "")
            if substr in n and n.endswith(".zip") and "win" in n and arch in n:
                return a.get("browser_download_url")
        return None

    def find_linux(substr: str) -> Optional[str]:
        for ext in (".tar.gz", ".zip"):
            for a in assets:
                n = a.get("name", "")
                if substr in n and n.endswith(ext) and "ubuntu" in n:
                    return a.get("browser_download_url")
        return None

    if sys.platform == "win32":
        if variant == "cpu":
            url = find_win(f"bin-win-cpu-{arch}")
            if url:
                return url
        if variant == "vulkan":
            url = find_win(f"bin-win-vulkan-{arch}")
            if url:
                return url
        if variant == "cuda":
            for cuda_tag in (
                f"bin-win-cuda-13.3-{arch}",
                f"bin-win-cuda-12.4-{arch}",
                f"bin-win-cuda-{arch}",
                "bin-win-cuda",
            ):
                url = find_win(cuda_tag)
                if url:
                    return url
        return find_win(f"bin-win-cpu-{arch}")

    if variant == "cuda":
        print("[vlm] Linux 공식 릴리스에 CUDA 빌드가 없어 vulkan/cpu 로 대체합니다.")
        variant = "vulkan"
    if variant == "vulkan":
        url = find_linux(f"bin-ubuntu-vulkan-{arch}")
        if url:
            return url
    url = find_linux(f"bin-ubuntu-{arch}")
    if url and "vulkan" not in Path(urlparse(url).path).name:
        return url
    for a in assets:
        n = a.get("name", "")
        if f"bin-ubuntu-{arch}" in n and "vulkan" not in n and "rocm" not in n:
            if n.endswith(".tar.gz") or n.endswith(".zip"):
                return a.get("browser_download_url")
    return find_linux(f"bin-ubuntu-{arch}")


def _get_release_download_url(variant: str) -> tuple[str, str]:
    tag = _env("LLAMA_CPP_RELEASE_TAG")
    if tag:
        if not tag.startswith("b"):
            tag = f"b{tag}"
        api_url = GITHUB_API.format(tag=f"tags/{tag}")
    else:
        api_url = GITHUB_API.format(tag="latest")
    release = _fetch_json(api_url)
    version = release.get("tag_name", "unknown")
    url = _pick_asset_url(release, variant)
    if not url:
        os_label = "Windows" if sys.platform == "win32" else "Linux(ubuntu)"
        raise RuntimeError(
            f"GitHub 릴리스 {version} 에서 variant={variant} {os_label} 패키지를 찾지 못했습니다. "
            "LLAMA_CPP_VARIANT=cpu|vulkan|cuda 를 바꿔 보세요."
        )
    return url, version


def _find_llama_server_exe() -> Optional[Path]:
    # PATH 우선
    which = shutil.which("llama-server") or shutil.which("llama-server.exe")
    if which:
        return Path(which)
    if not LLAMA_DIR.is_dir():
        return None
    for name in ("llama-server.exe", "llama-server"):
        for p in LLAMA_DIR.rglob(name):
            if p.is_file():
                return p
    return None


def _extract_archive(archive: Path, dest: Path) -> None:
    print(f"[vlm] 압축 해제: {dest}")
    if archive.suffixes[-2:] == [".tar", ".gz"] or archive.name.endswith(".tar.gz"):
        with tarfile.open(archive, "r:gz") as tf:
            tf.extractall(dest)
    elif archive.suffix.lower() == ".zip":
        with zipfile.ZipFile(archive, "r") as zf:
            zf.extractall(dest)
    else:
        raise RuntimeError(f"지원하지 않는 압축 형식: {archive.name}")


def _install_llama_cpp(variant: str) -> Path:
    existing = _find_llama_server_exe()
    if existing:
        return existing

    url, version = _get_release_download_url(variant)
    suffix = ".tar.gz" if url.endswith(".tar.gz") else ".zip"
    LLAMA_DIR.mkdir(parents=True, exist_ok=True)
    archive_path = LLAMA_DIR / f"llama-{version}-{variant}{suffix}"
    _download(url, archive_path, f"llama.cpp {version} ({variant})")

    extract_to = LLAMA_DIR / version
    if extract_to.is_dir():
        shutil.rmtree(extract_to, ignore_errors=True)
    extract_to.mkdir(parents=True, exist_ok=True)
    _extract_archive(archive_path, extract_to)
    try:
        archive_path.unlink()
    except OSError:
        pass

    META_PATH.write_text(
        json.dumps(
            {
                "version": version,
                "variant": variant,
                "platform": sys.platform,
                "arch": _cpu_arch(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    exe = _find_llama_server_exe()
    if not exe:
        raise RuntimeError("압축 해제 후 llama-server 를 찾지 못했습니다.")
    if sys.platform != "win32":
        try:
            exe.chmod(exe.stat().st_mode | 0o111)
        except OSError:
            pass
    return exe


def _start_vlm_server(exe: Path, host: str, port: int) -> None:
    bind = "127.0.0.1" if host in ("0.0.0.0", "::", "127.0.0.1") else host
    variant = _env("LLAMA_CPP_VARIANT") or _detect_variant()
    ngl = _env("LLAMA_NGL", "")
    if not ngl:
        ngl = "0" if variant == "cpu" else "999"

    cmd = [
        str(exe),
        "-hf",
        VLM_HF_REPO,
        "--host",
        bind,
        "--port",
        str(port),
        "-ngl",
        ngl,
    ]
    threads = _env("LLAMA_THREADS", "")
    if threads:
        cmd += ["-t", threads]
    threads_batch = _env("LLAMA_THREADS_BATCH", "")
    if threads_batch:
        cmd += ["-tb", threads_batch]

    print(f"[vlm] 모델(-hf): {VLM_HF_REPO}")
    print("[vlm] 서버 시작:")
    print("      " + " ".join(cmd))

    creationflags = 0
    stdout = None
    stderr = None
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NEW_CONSOLE
    else:
        LLAMA_DIR.mkdir(parents=True, exist_ok=True)
        log_path = LLAMA_DIR / "llama-vlm-server.log"
        log_f = open(log_path, "a", encoding="utf-8")
        stdout = log_f
        stderr = subprocess.STDOUT
        print(f"[vlm] 로그: {log_path}")

    subprocess.Popen(
        cmd,
        cwd=str(exe.parent),
        creationflags=creationflags,
        stdout=stdout,
        stderr=stderr,
        start_new_session=(sys.platform != "win32"),
    )


def _wait_healthy(host: str, port: int, seconds: int = 600) -> bool:
    print(f"[vlm] 서버 기동 대기 (최대 {seconds}초, 첫 실행은 모델 다운로드 포함)...")
    deadline = time.time() + seconds
    while time.time() < deadline:
        if server_healthy(host, port):
            print(f"[vlm] 준비 완료: http://{host}:{port}")
            return True
        time.sleep(3)
    return False


def main() -> int:
    if sys.platform not in ("win32", "linux"):
        print(
            f"[vlm] 자동 기동은 Windows/Linux 만 지원합니다 (현재: {sys.platform}). "
            f"수동 실행: {vlm_server_cmd_hint()}"
        )
        return 1

    host, port = _parse_host_port()
    if server_healthy(host, port):
        print(f"[vlm] 이미 실행 중: http://{host}:{port}")
        return 0

    print(f"[vlm] VL 서버 미응답 (port {port}) — 설치·기동을 시도합니다.")
    try:
        variant = _detect_variant()
        print(f"[vlm] 빌드 variant: {variant}")
        exe = _install_llama_cpp(variant)
        _start_vlm_server(exe, host, port)
    except Exception as e:
        print(f"[오류] VLM llama-server 준비 실패: {e}", file=sys.stderr)
        print(f"        수동 실행: {vlm_server_cmd_hint()}", file=sys.stderr)
        return 1

    if _wait_healthy(host, port):
        return 0

    log_hint = (
        f" ({LLAMA_DIR / 'llama-vlm-server.log'})" if sys.platform != "win32" else "."
    )
    print(
        f"[오류] VL llama-server 가 시간 내에 응답하지 않습니다. 로그를 확인하세요{log_hint}",
        file=sys.stderr,
    )
    print(f"        수동 실행: {vlm_server_cmd_hint()}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
