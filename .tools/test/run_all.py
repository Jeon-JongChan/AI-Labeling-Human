"""
모든 단위/통합 테스트를 한 번에 실행하는 일괄 테스트 러너.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def run_test_file(path: Path) -> bool:
    """개별 테스트 스크립트를 독립 프로세스로 실행."""
    print(f"\n>> 실행 중: {path.name} ...")
    res = subprocess.run(
        [sys.executable, str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if res.stdout:
        print(res.stdout.strip())
    if res.returncode != 0:
        print(f"[실패] {path.name} (종료 코드 {res.returncode})", file=sys.stderr)
        if res.stderr:
            print(res.stderr.strip(), file=sys.stderr)
        return False
    return True


def main() -> None:
    """모든 test_*.py 스크립트 탐색 및 실행."""
    test_files = sorted(
        f for f in HERE.glob("test_*.py") if f.is_file()
    )

    if not test_files:
        print("실행할 테스트 파일이 없습니다.")
        sys.exit(1)

    print("==================================================")
    print(f" [통합 테스트 실행] 총 {len(test_files)}개 테스트 스위트")
    print("==================================================")

    passed = 0
    failed = 0

    for tf in test_files:
        ok = run_test_file(tf)
        if ok:
            passed += 1
        else:
            failed += 1

    print("\n==================================================")
    print(f" [결과 요약] 성공: {passed}개, 실패: {failed}개")
    print("==================================================")

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()

