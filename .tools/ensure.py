"""
라벨러 실행 환경 사전 점검 및 통합 준비 스크립트.

라벨러 구동에 필요한 3가지 핵심 런타임 환경을 점검하고 자동으로 구성합니다:
1. GPU (CUDA PyTorch 가속 환경)
2. Java (JRE 17 / OpenDataLoader PDF 추출 엔진)
3. VL Server (llama.cpp 기반 비전-언어 모델 서버)

사용법:
    python ensure.py         # 전체 환경 순차 점검 (GPU -> Java -> VLM)
    python ensure.py gpu     # GPU/CUDA 환경만 점검 및 설치
    python ensure.py java    # Java 환경만 점검 및 포터블 JRE 구성
    python ensure.py vlm     # VL 서버 상태 확인, 설치 및 백그라운드 기동
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

# Windows 콘솔 utf-8 출력 설정
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

TOOLS_DIR = Path(__file__).resolve().parent
TOOL_DIR = TOOLS_DIR.parent
APP_DIR = TOOL_DIR / "app"
sys.path.insert(0, str(APP_DIR))
sys.path.insert(0, str(TOOL_DIR))


# ============================================================================
# 1. GPU (CUDA PyTorch) 점검 및 자동 설치
# ============================================================================

def check_nvidia_smi() -> bool:
    """nvidia-smi 명령 실행 가능 여부로 물리적 NVIDIA GPU 장착을 확인합니다."""
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

    Returns:
        bool: GPU 가속 활성화 여부
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


# ============================================================================
# 2. Java (JRE 17 / OpenDataLoader용) 점검 및 자동 구성
# ============================================================================

JAVA_DIR = TOOLS_DIR / "java"
JAVA_BIN = JAVA_DIR / "bin" / ("java.exe" if sys.platform == "win32" else "java")

# Adoptium Temurin 17 공식 JRE 다운로드 URL (Windows x64)
ADOPTIUM_JRE_URL = (
    "https://api.adoptium.net/v3/binary/latest/17/ga/windows/x64/jre/hotspot/normal/eclipse"
)

# 로컬 PC 내 기존 JDK 후보 경로
LOCAL_JDK_CANDIDATES = [
    Path(r"D:\Projects\Nkami\java\jdk-17.0.12"),
    Path(r"D:\Projects\Nkami\01.sts-4.21.0.RELEASE\plugins\org.eclipse.justj.openjdk.hotspot.jre.full.win32.x86_64_17.0.9.v20231028-0858\jre"),
]


def find_system_java() -> Optional[str]:
    """시스템 PATH 또는 JAVA_HOME에서 java 실행 파일을 검색합니다."""
    # 1. JAVA_HOME 우선 검사
    java_home = os.environ.get("JAVA_HOME")
    if java_home:
        bin_path = Path(java_home) / "bin" / ("java.exe" if sys.platform == "win32" else "java")
        if bin_path.is_file():
            return str(bin_path)

    # 2. PATH 검색
    return shutil.which("java")


def setup_local_candidate(src_path: Path) -> bool:
    """PC 내 존재하는 JDK 경로를 tools/labeler/java 에 정션(junction)으로 연결합니다."""
    try:
        if JAVA_DIR.exists():
            return True

        if sys.platform == "win32":
            # 윈도우 디렉토리 정션(junction) 생성 — 관리자 권한 불필요
            cmd = f'cmd /c mklink /J "{JAVA_DIR}" "{src_path}"'
            res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            if res.returncode == 0 and JAVA_BIN.is_file():
                print(f"[labeler] 로컬 JDK 연결 완료: {src_path} -> {JAVA_DIR}", flush=True)
                return True
        # 정션 실패 시 디렉토리 심볼릭 링크 또는 복사 시도
        JAVA_DIR.symlink_to(src_path, target_is_directory=True)
        return JAVA_BIN.is_file()
    except Exception as exc:
        print(f"[labeler] 로컬 JDK 정션 실패 ({exc}), 복사 모드로 시도합니다.", flush=True)
        try:
            shutil.copytree(src_path, JAVA_DIR)
            return JAVA_BIN.is_file()
        except Exception as copy_exc:
            print(f"[labeler] 로컬 JDK 복사 실패: {copy_exc}", flush=True)
            return False


def download_portable_jre() -> bool:
    """공식 Adoptium 저장소에서 경량 JRE 17 zip을 다운로드받아 tools/labeler/java 에 압축 해제합니다."""
    import requests

    print(f"[labeler] 포터블 Java(JRE 17) 다운로드 시작: {ADOPTIUM_JRE_URL} ...", flush=True)
    temp_zip = TOOLS_DIR / "jre_temp.zip"
    try:
        resp = requests.get(ADOPTIUM_JRE_URL, stream=True, timeout=120)
        resp.raise_for_status()

        with open(temp_zip, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                if chunk:
                    f.write(chunk)

        print("[labeler] 다운로드 완료, 압축 해제 중...", flush=True)
        extract_dir = TOOLS_DIR / "jre_extract"
        shutil.rmtree(extract_dir, ignore_errors=True)

        with zipfile.ZipFile(temp_zip, "r") as zf:
            zf.extractall(extract_dir)

        # 압축 해제된 루트 폴더 탐색 (보통 jdk-17.x-jre)
        subdirs = [d for d in extract_dir.iterdir() if d.is_dir()]
        root_dir = subdirs[0] if subdirs else extract_dir

        if JAVA_DIR.exists():
            shutil.rmtree(JAVA_DIR, ignore_errors=True)

        shutil.move(str(root_dir), str(JAVA_DIR))
        shutil.rmtree(extract_dir, ignore_errors=True)
        temp_zip.unlink(missing_ok=True)

        print(f"[labeler] 포터블 JRE 준비 완료: {JAVA_DIR}", flush=True)
        return JAVA_BIN.is_file()
    except Exception as exc:
        print(f"[labeler] JRE 다운로드 실패: {exc}", flush=True)
        temp_zip.unlink(missing_ok=True)
        return False


def ensure_java() -> Optional[str]:
    """
    Java 환경을 점검하고 필요 시 tools/labeler/java 에 포터블 환경을 구축합니다.

    Returns:
        Optional[str]: 유효한 Java 바이너리 경로 또는 None
    """
    # 1. tools/labeler/java/bin/java.exe 가 이미 존재하는 경우
    if JAVA_BIN.is_file():
        return str(JAVA_BIN)

    # 2. 시스템에 이미 Java가 있는 경우
    sys_java = find_system_java()
    if sys_java:
        return sys_java

    print("[labeler] 시스템 Java 미감지 - 포터블 Java 환경을 구성합니다...", flush=True)

    # 3. 로컬 PC의 기존 JDK 후보 우선 연결
    for candidate in LOCAL_JDK_CANDIDATES:
        cand_bin = candidate / "bin" / ("java.exe" if sys.platform == "win32" else "java")
        if cand_bin.is_file():
            if setup_local_candidate(candidate):
                return str(JAVA_BIN)

    # 4. 후보가 없으면 원격에서 공식 JRE 17 다운로드
    if download_portable_jre():
        return str(JAVA_BIN)

    print("[labeler] [경고] 포터블 Java 구성 실패 - pypdfium2 내장 엔진으로 동작합니다.", flush=True)
    return None


# ============================================================================
# 3. VL Server (llama-server) 점검, 설치 및 백그라운드 기동
# ============================================================================

# 설치 위치(없을 때만). 탐색은 여기 + 상위 local-rag/tools/llama-cpp 도 포함
LLAMA_DIR = TOOLS_DIR / "llama-cpp"
META_PATH = LLAMA_DIR / ".install_meta.json"
USER_AGENT = "ai-labeling-human-ensure-vlm/1.0"
GITHUB_API_LIST = "https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=30"
GITHUB_API_TAG = "https://api.github.com/repos/ggml-org/llama.cpp/releases/tags/{tag}"


def _env(name: str, default: str = "") -> str:
    """환경 변수를 조회하고 좌우 공백을 제거합니다."""
    return os.getenv(name, default).strip()


def _parse_host_port() -> tuple[str, int]:
    """VLM_BASE_URL 또는 VLM_PORT 로부터 호스트와 포트를 파싱합니다."""
    from config import VLM_BASE_URL, VLM_PORT
    parsed = urlparse(VLM_BASE_URL.rstrip("/") + "/")
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or VLM_PORT
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"
    return host, port


def _port_listening(host: str, port: int, timeout: float = 1.0) -> bool:
    """지정된 호스트 및 포트의 연결 가능 여부를 확인합니다."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _http_ok(url: str, timeout: float = 3.0) -> bool:
    """HTTP 2xx 응답 여부를 확인합니다."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def server_healthy(host: str, port: int) -> bool:
    """VL 서버의 헬스체크 엔드포인트를 확인합니다."""
    if not _port_listening(host, port):
        return False
    for path in ("/health", "/v1/models", "/"):
        if _http_ok(f"http://{host}:{port}{path}"):
            return True
    return False


def _confirm_server_start(host: str, port: int) -> bool:
    """서버 설치·모델 다운로드·기동을 사용자에게 확인받습니다."""
    print(f"[vlm] VL 서버가 실행 중이 아닙니다: http://{host}:{port}")
    print(
        "[vlm] llama.cpp 설치와 VLM 모델 다운로드를 포함한 서버 기동을 "
        "진행할까요? [y/N]: ",
        end="",
        flush=True,
    )
    try:
        answer = input().strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\n[vlm] 사용자 입력이 없어 서버를 기동하지 않습니다.")
        return False
    if answer in {"y", "yes", "예", "네"}:
        return True
    print("[vlm] 서버 기동을 건너뜁니다. labeler 웹은 계속 실행됩니다.")
    return False


def _download(url: str, dest: Path, label: str) -> None:
    """원격 URL의 파일을 스트리밍 방식으로 다운로드합니다."""
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
    """원격 JSON API를 호출하여 파싱된 딕셔너리를 반환합니다."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _cpu_arch() -> str:
    """현재 시스템의 CPU 아키텍처를 표준 식별자(x64, arm64 등)로 반환합니다."""
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        return "x64"
    if machine in ("aarch64", "arm64"):
        return "arm64"
    return machine


def _detect_variant() -> str:
    """환경 변수 및 GPU 탑재 여부를 기반으로 적합한 llama.cpp 빌드(cuda/vulkan/cpu)를 판별합니다."""
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
    """GitHub 릴리스 목록에서 플랫폼 및 아키텍처에 맞는 다운로드 URL을 탐색합니다."""
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
    """바이너리가 존재하는 최신 또는 지정 릴리스의 다운로드 URL을 획득합니다."""
    tag = _env("LLAMA_CPP_RELEASE_TAG")
    if tag:
        if not tag.startswith("b") and not tag.startswith("v"):
            tag = f"b{tag}"
        release = _fetch_json(GITHUB_API_TAG.format(tag=tag))
        version = release.get("tag_name", tag)
        url = _pick_asset_url(release, variant)
        if not url:
            os_label = "Windows" if sys.platform == "win32" else "Linux(ubuntu)"
            raise RuntimeError(
                f"GitHub 릴리스 {version} 에서 variant={variant} {os_label} 패키지를 찾지 못했습니다. "
                "LLAMA_CPP_VARIANT=cpu|vulkan|cuda 를 바꿔 보세요."
            )
        return url, version

    releases = _fetch_json(GITHUB_API_LIST)
    if not isinstance(releases, list):
        releases = []
    for release in releases:
        url = _pick_asset_url(release, variant)
        if url:
            version = release.get("tag_name", "unknown")
            return url, version

    os_label = "Windows" if sys.platform == "win32" else "Linux(ubuntu)"
    raise RuntimeError(
        f"최근 llama.cpp 릴리스에서 variant={variant} {os_label} 패키지를 찾지 못했습니다. "
        "LLAMA_CPP_VARIANT=cpu|vulkan|cuda 를 바꿔 보세요."
    )


def _llama_search_dirs() -> list[Path]:
    """llama-server 를 검색할 대상 디렉토리 목록을 반환합니다."""
    dirs: list[Path] = []
    env = _env("LLAMA_CPP_DIR")
    if env:
        dirs.append(Path(env).expanduser().resolve())
    dirs.append(LLAMA_DIR)
    if (TOOL_DIR / "llama-cpp").is_dir():
        dirs.append(TOOL_DIR / "llama-cpp")
    # tools/labeler → ../llama-cpp (local-rag 의 tools/llama-cpp, 예: b9859)
    sibling = TOOL_DIR.parent / "llama-cpp"
    if sibling.resolve() not in {p.resolve() for p in dirs}:
        dirs.append(sibling)
    return dirs


def _find_llama_server_exe() -> Optional[Path]:
    """시스템 PATH 또는 llama-cpp 디렉토리 내에서 llama-server 실행 파일을 찾습니다."""
    names = ("llama-server.exe", "llama-server")
    which = shutil.which("llama-server") or shutil.which("llama-server.exe")
    if which:
        return Path(which)
    for root in _llama_search_dirs():
        if not root.is_dir():
            continue
        for name in names:
            direct = root / name
            if direct.is_file():
                return direct
            for p in root.rglob(name):
                if p.is_file():
                    return p
    return None


def _extract_archive(archive: Path, dest: Path) -> None:
    """압축 파일(.zip 또는 .tar.gz)을 대상 디렉토리에 해제합니다."""
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
    """필요한 llama.cpp 바이너리를 다운로드 및 설치하고 실행 파일 경로를 반환합니다."""
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
    """llama-server 프로세스를 백그라운드 또는 별도 콘솔로 시작합니다."""
    from config import VLM_HF_REPO

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
    cache = _env("LLAMA_CACHE")
    if cache:
        print(f"[vlm] 모델 캐시: {cache}")
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
    """VL 서버가 정상 응답할 때까지 대기합니다."""
    print(f"[vlm] 서버 기동 대기 (최대 {seconds}초, 첫 실행은 모델 다운로드 포함)...")
    deadline = time.time() + seconds
    while time.time() < deadline:
        if server_healthy(host, port):
            print(f"[vlm] 준비 완료: http://{host}:{port}")
            return True
        time.sleep(3)
    return False


def ensure_vlm_server() -> int:
    """
    VL 서버 상태를 확인하고 필요 시 설치 및 백그라운드 기동을 수행합니다.

    Returns:
        int: 정상 기동 또는 건너뜀 시 0, 오류 시 1
    """
    from config import GEMINI_API_KEY, vlm_server_cmd_hint

    if GEMINI_API_KEY:
        print("[vlm] GEMINI_API_KEY 가 설정되어 있어 로컬 VL 서버 기동을 건너뜁니다.")
        return 0
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

    if not _confirm_server_start(host, port):
        return 1

    print(f"[vlm] VL 서버 미응답 (port {port}) — 설치·기동을 시작합니다.")
    try:
        variant = _detect_variant()
        print(f"[vlm] 빌드 variant: {variant}")
        exe = _install_llama_cpp(variant)
        print(f"[vlm] llama-server: {exe}")
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


# ============================================================================
# CLI 엔트리포인트 (통합 실행 및 개별 타겟 실행)
# ============================================================================

def main() -> int:
    """
    명령줄 인자에 따라 환경 점검 작업을 분기 실행합니다.

    - 인자 없음 또는 'all': GPU -> Java -> VLM 순차 점검
    - 'gpu': GPU/CUDA 점검 및 PyTorch 설치
    - 'java': Java/JRE 점검 및 포터블 환경 연결
    - 'vlm': VL 서버 상태 점검 및 기동
    """
    target = sys.argv[1].lower() if len(sys.argv) > 1 else "all"

    if target in ("-h", "--help", "help"):
        print(__doc__.strip())
        return 0

    if target == "gpu":
        print("[ensure] GPU(CUDA) 환경 점검...", flush=True)
        ensure_gpu_torch()
        return 0

    if target == "java":
        print("[ensure] Java(JRE 17) 환경 점검...", flush=True)
        path = ensure_java()
        if path:
            print(f"[labeler] Java 준비됨: {path}")
        else:
            print("[labeler] Java 사용 불가 (내장 엔진 사용)")
        return 0

    if target == "vlm":
        print("[ensure] VL 서버 환경 점검...", flush=True)
        return ensure_vlm_server()

    if target == "all":
        print("[ensure] 전체 환경 순차 점검 시작 (GPU -> Java -> VLM)", flush=True)
        # 1. GPU
        print("\n--- 1. GPU 확인 ---", flush=True)
        ensure_gpu_torch()

        # 2. Java
        print("\n--- 2. Java 확인 ---", flush=True)
        java_path = ensure_java()
        if java_path:
            print(f"[labeler] Java 준비됨: {java_path}")
        else:
            print("[labeler] Java 사용 불가 (내장 엔진 사용)")

        # 3. VL Server
        print("\n--- 3. VL 서버 확인 ---", flush=True)
        vlm_code = ensure_vlm_server()
        return vlm_code

    print(f"[ensure] 알 수 없는 타겟입니다: '{target}'. (가능한 타겟: gpu, java, vlm, all)", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

