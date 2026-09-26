"""Production starts through uvicorn-worker (Batch 25, ADR 0013).

Render starts the app with gunicorn and a uvicorn worker class. The class it
named, uvicorn.workers.UvicornWorker, is deprecated in the pinned uvicorn in
favour of the uvicorn-worker package, and an upgrade that drops it would stop
production at start-up. The tests never start gunicorn, so they did not see it.
gunicorn cannot be imported on Windows; there the package is only looked up,
and CI on Linux imports it for real.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
START = "gunicorn -k uvicorn_worker.UvicornWorker app.main:app"


def test_uvicorn_worker_is_pinned_and_installed():
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    pinned = re.search(r"^uvicorn-worker==\d", requirements, re.M)
    assert pinned, "requirements.txt does not pin uvicorn-worker"
    assert importlib.util.find_spec("uvicorn_worker") is not None, "uvicorn-worker is not installed"


def test_the_worker_class_loads_without_a_deprecation():
    if sys.platform == "win32":  # gunicorn does not import on Windows
        assert importlib.util.find_spec("uvicorn_worker") is not None
        return
    import uvicorn_worker  # a DeprecationWarning is an error in this suite

    assert hasattr(uvicorn_worker, "UvicornWorker")


def test_the_readme_gives_the_start_command_render_must_use():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert START in readme
    assert "uvicorn.workers.UvicornWorker" not in readme
