"""
ensure.py 기본 모듈 및 인터페이스 동작 검증 스크립트.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS_DIR = HERE.parent

if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from ensure import _parse_host_port


def test_ensure() -> None:
    """ensure.py 기본 유틸리티 및 CLI 호출 검증."""
    # 1. Host/Port 파싱 검증
    host, port = _parse_host_port()
    assert isinstance(host, str) and len(host) > 0, "Host parsing failed"
    assert isinstance(port, int) and port > 0, "Port parsing failed"

    ensure_script = str(TOOLS_DIR / "ensure.py")

    # 2. CLI --help 호출 검증
    res_help = subprocess.run(
        [sys.executable, ensure_script, "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert res_help.returncode == 0, f"--help failed: {res_help.stderr}"
    assert "라벨러 실행 환경 사전 점검" in res_help.stdout, "Help output missing header"

    # 3. CLI java 호출 검증
    res_java = subprocess.run(
        [sys.executable, ensure_script, "java"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert res_java.returncode == 0, f"java check failed: {res_java.stderr}"
    assert "Java(JRE 17) 환경 점검" in res_java.stdout, "Java check output missing expected string"

    print("[통과] ensure 환경 점검 테스트 완료")


if __name__ == "__main__":
    test_ensure()

