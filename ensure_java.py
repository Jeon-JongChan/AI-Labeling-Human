"""
포터블 Java(JRE) 환경 구성 스크립트.

시스템 Java 미설치 시 tools/labeler/java 경로에 포터블 JRE를 구성하여
OpenDataLoader가 외부 의존성 없이 독립적으로 실행될 수 있도록 보장합니다.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Optional

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

TOOL_DIR = Path(__file__).resolve().parent
JAVA_DIR = TOOL_DIR / "java"
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
    temp_zip = TOOL_DIR / "jre_temp.zip"
    try:
        resp = requests.get(ADOPTIUM_JRE_URL, stream=True, timeout=120)
        resp.raise_for_status()

        with open(temp_zip, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                if chunk:
                    f.write(chunk)

        print("[labeler] 다운로드 완료, 압축 해제 중...", flush=True)
        extract_dir = TOOL_DIR / "jre_extract"
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
    최종 Java 실행 파일 경로를 반환합니다.
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


if __name__ == "__main__":
    path = ensure_java()
    if path:
        print(f"[labeler] Java 준비됨: {path}")
    else:
        print("[labeler] Java 사용 불가 (내장 엔진 사용)")
