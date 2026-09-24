"""Runs the test suite and passes only if exactly the known failures fail.

A batch may carry failing tests on purpose until they are fixed; they are
listed in tests/known_failures.txt. On fix/** branches CI runs this instead of
plain pytest, so a red job always means something new: a test that should pass
failed, or a known failure started to pass and its line has to go. main and
pull requests into main run plain pytest, where nothing may fail. The same
script is the gate before every commit.

    python scripts/check_known_failures.py [extra pytest arguments]
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KNOWN = ROOT / "tests" / "known_failures.txt"
SUMMARY = re.compile(r"^(FAILED|ERROR) (\S.*?)(?: - .*)?$")


def read_known(path: Path = KNOWN) -> set[str]:
    """Node ids in the list; blank lines and # comments are ignored."""
    if not path.exists():
        return set()
    lines = (line.strip() for line in path.read_text(encoding="utf-8").splitlines())
    return {line for line in lines if line and not line.startswith("#")}


def parse_failures(output: str) -> tuple[set[str], set[str]]:
    """Node ids from pytest's short summary (-rfE), as (failed, errors)."""
    failed: set[str] = set()
    errors: set[str] = set()
    for line in output.splitlines():
        match = SUMMARY.match(line.rstrip())
        if match:
            (failed if match.group(1) == "FAILED" else errors).add(match.group(2))
    return failed, errors


def compare(known: set[str], failed: set[str], errors: set[str]) -> list[str]:
    """Every way the run differs from the list, one line each."""
    problems = [f"error (setup or teardown broke), never a known failure: {node}" for node in sorted(errors)]
    problems += [f"failed, but is not a known failure: {node}" for node in sorted(failed - known)]
    problems += [f"known failure that did not fail (fixed? then remove its line): {node}"
                 for node in sorted(known - failed)]
    return problems


def run_pytest(extra: list[str]) -> tuple[int, str]:
    """Runs pytest, streams its output as it comes, and returns it."""
    command = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-rfE", *extra]
    process = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, encoding="utf-8", errors="replace", bufsize=1)
    lines = []
    for line in process.stdout:
        sys.stdout.write(line)
        lines.append(line)
    return process.wait(), "".join(lines)


def main(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    known = read_known()
    code, output = run_pytest(argv)
    print()
    if code not in (0, 1):
        print(f"KNOWN-FAILURES GATE: FAIL. pytest stopped abnormally (exit code {code}).")
        return code
    failed, errors = parse_failures(output)
    problems = compare(known, failed, errors)
    if problems:
        print("KNOWN-FAILURES GATE: FAIL")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(f"KNOWN-FAILURES GATE: OK. Exactly the {len(known)} known failure(s) failed; "
          "everything else passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
