"""The documentation is part of the change (Definition of Done, Batch 22).

Architecture decisions are recorded as ADRs, each with its status, context,
decision and consequences, numbered in order and listed in docs/adr/README.md.
The coverage floor CONSTRAINTS.md promises is the one CI enforces, so neither
can change without the other.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADR_DIR = ROOT / "docs" / "adr"
SECTIONS = ("## Context", "## Decision", "## Consequences")


def adrs() -> list[Path]:
    return sorted(ADR_DIR.glob("[0-9][0-9][0-9][0-9]-*.md"))


def test_every_decision_is_recorded_with_its_reasons():
    found = adrs()
    assert len(found) >= 10, f"{len(found)} ADRs in docs/adr"
    incomplete = []
    for path in found:
        text = path.read_text(encoding="utf-8")
        if not all(section in text for section in SECTIONS) or not re.search(r"^Status: \w", text, re.M):
            incomplete.append(path.name)
    assert not incomplete, f"ADRs without a status, context, decision or consequences: {incomplete}"


def test_the_adrs_are_numbered_in_order_and_all_listed():
    found = adrs()
    numbers = [int(path.name[:4]) for path in found]
    assert found and numbers == list(range(1, len(found) + 1)), numbers
    index = (ADR_DIR / "README.md").read_text(encoding="utf-8")
    missing = [path.name for path in found if path.name not in index]
    assert not missing, f"not listed in docs/adr/README.md: {missing}"


def test_ci_enforces_the_coverage_floor_constraints_promises():
    constraints = (ROOT / "CONSTRAINTS.md").read_text(encoding="utf-8")
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    promised = re.search(r"^\| Coverage \|[^|]*?(\d+) %", constraints, re.M)
    enforced = re.search(r"--cov-fail-under=(\d+)", ci)
    assert promised and enforced, "a coverage floor in CONSTRAINTS.md, and --cov-fail-under in CI"
    assert promised.group(1) == enforced.group(1), (promised.group(1), enforced.group(1))


def test_the_definition_of_done_is_written_down():
    path = ROOT / "docs" / "DEFINITION_OF_DONE.md"
    assert path.exists(), "docs/DEFINITION_OF_DONE.md does not exist yet"
    text = path.read_text(encoding="utf-8")
    assert "check_known_failures" in text and "ADR" in text
