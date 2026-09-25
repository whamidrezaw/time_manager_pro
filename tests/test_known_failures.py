"""The gate for a batch that carries failing tests on purpose.

scripts/check_known_failures.py passes only if exactly the tests listed in
tests/known_failures.txt fail. CI runs it on fix/** branches, and it is the
gate before every commit. These tests pin how it reads pytest's summary, how
it compares, and that CI uses it only where known failures are allowed.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_known_failures.py"
LIST = ROOT / "tests" / "known_failures.txt"
CI = ROOT / ".github" / "workflows" / "ci.yml"

A = "tests/test_x.py::test_a"
B = "tests/browser/test_y.py::test_b[list-light]"
C = "tests/test_x.py::test_c"


def load_gate():
    if not SCRIPT.exists():
        pytest.fail("scripts/check_known_failures.py does not exist yet")
    spec = importlib.util.spec_from_file_location("check_known_failures", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_summary_lines_give_the_node_ids():
    gate = load_gate()
    output = "\n".join([
        f"FAILED {B} - AssertionError: axe found 3 violations",
        f"FAILED {A}",
        f"ERROR {C} - Failed: the page raised a CSP violation",
        "10 failed, 368 passed, 1 error in 80.01s",
    ])
    assert gate.parse_failures(output) == ({A, B}, {C})


def test_exactly_the_known_failures_is_a_pass():
    gate = load_gate()
    assert gate.compare(known={A, B}, failed={A, B}, errors=set()) == []


def test_a_new_failure_is_reported_by_name():
    gate = load_gate()
    problems = gate.compare(known={A}, failed={A, C}, errors=set())
    assert len(problems) == 1 and C in problems[0]


def test_a_known_failure_that_passes_is_reported_so_its_line_goes():
    gate = load_gate()
    problems = gate.compare(known={A, B}, failed={A}, errors=set())
    assert len(problems) == 1 and B in problems[0]


def test_an_error_is_never_a_known_failure():
    gate = load_gate()
    """An error means setup or teardown broke: something else went wrong."""
    problems = gate.compare(known={A}, failed={A}, errors={A})
    assert problems and all(A in p for p in problems)


def test_the_list_ignores_comments_and_blank_lines(tmp_path):
    gate = load_gate()
    listing = tmp_path / "known.txt"
    listing.write_text(f"# why these fail\n\n{A}\n  {B}  \n# A11Y-07\n", encoding="utf-8")
    assert gate.read_known(listing) == {A, B}


def test_every_listed_failure_names_a_test_that_exists():
    """A typo in the list must not become a silent pass."""
    assert LIST.exists(), "tests/known_failures.txt does not exist yet"
    entries = [line.strip() for line in LIST.read_text(encoding="utf-8").splitlines()]
    entries = [e for e in entries if e and not e.startswith("#")]
    broken = [e for e in entries if "::" not in e or not (ROOT / e.split("::")[0]).exists()]
    # An empty list is where every batch ends (Batch 20 was the first to get there).
    assert not broken, f"entries that name no test file: {broken}"


def test_ci_runs_the_full_suite_on_main_and_the_gate_elsewhere():
    """main and pull requests into main allow no failure at all; only fix/**
    branches may carry the listed ones."""
    steps = CI.read_text(encoding="utf-8").split("- name:")
    full = [s for s in steps if "run: pytest -v" in s]
    gated = [s for s in steps if "python scripts/check_known_failures.py" in s]

    strict = "if: github.ref == 'refs/heads/main' || github.event_name == 'pull_request'"
    batch = "if: github.ref != 'refs/heads/main' && github.event_name != 'pull_request'"
    assert len(full) == 1 and strict in full[0]
    assert len(gated) == 1 and batch in gated[0]
