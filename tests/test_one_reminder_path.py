"""One reminder path (Batch 24, ADR 0012).

Reminders are sent by POST /tasks/run-reminders, called every minute by an
external cron, with the GitHub Action as a slower fallback. The long-running
worker they replaced, and the Docker, Fly.io and systemd files that deployed
it, were kept after nothing ran them; Render runs the web service as native
Python. They are gone, and nothing may still point at them: a README that
describes a deployment which does not exist is worse than none.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REMOVED = ["Dockerfile", ".dockerignore", "fly.toml", "deploy/systemd",
           "deploy/run-worker.sh", "worker/reminder_worker.py"]
NAMES = ["reminder_worker", "fly.toml", "Fly.io", "systemd", "Dockerfile",
         "run-worker.sh", "REMINDER_POLL_INTERVAL", "reminder_poll_interval"]
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", ".ruff_cache"}
TEXT = {".py", ".md", ".yml", ".yaml", ".toml", ".txt", ".example", ".sh", ".cfg", ".ini"}


def test_the_old_worker_and_its_deploy_files_are_gone():
    left = [path for path in REMOVED if (ROOT / path).exists()]
    assert not left, f"still here: {left}"


def test_nothing_still_points_at_them():
    this = Path(__file__).resolve()
    adr = ROOT / "docs" / "adr"  # the records may name what was removed: they are history
    # A step script (apply_*.py) names what it removes while it runs, then
    # removes itself; ruff leaves it out for the same reason.
    pointers = []
    for path in ROOT.rglob("*"):
        step_script = path.parent == ROOT and path.name.startswith("apply_") and path.suffix == ".py"
        if (not path.is_file() or path.resolve() == this or adr in path.parents or step_script
                or SKIP_DIRS & set(path.relative_to(ROOT).parts)
                or (path.suffix not in TEXT and not path.name.startswith(".env"))):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        pointers += [f"{path.relative_to(ROOT)}: {name}" for name in NAMES if name in text]
    assert not pointers, pointers
