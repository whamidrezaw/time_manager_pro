"""The release (Batch 32; docs/RELEASE.md).

One version, in one place in the code, which FastAPI reports and the start-up
line names, and which the top entry of CHANGELOG.md has to match, so a release
cannot ship with its notes describing another. RELEASE.md holds the checklist
a version passes before its tag, and how to roll one back.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_one_version_everywhere():
    import app
    import app.main as main_module

    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    top = re.search(r"^## \[(\d+\.\d+\.\d+)\] - \d{4}-\d{2}-\d{2}$", changelog, re.M)
    assert top, "CHANGELOG.md has no dated release entry"
    assert app.__version__ == top.group(1) == main_module.app.version, (
        app.__version__, top.group(1), main_module.app.version)


def test_the_changelog_says_what_changed_in_the_usual_sections():
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    first = changelog.split("\n## [", 2)[1]
    for section in ("### Added", "### Changed", "### Fixed", "### Security", "### Removed"):
        assert section in first, section


def test_the_release_guide_has_its_checklist_and_a_way_back():
    guide = (ROOT / "docs" / "RELEASE.md").read_text(encoding="utf-8")
    for heading in ("## Before the tag", "## Tagging", "## After the deploy", "## Rolling back"):
        assert heading in guide, heading
