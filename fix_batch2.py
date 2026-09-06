#!/usr/bin/env python3
"""
fix_batch2.py — TimeManager Pro, batch 2: repository hygiene.

Run once from the repository root:

    python fix_batch2.py

Safe to run twice: every step checks the current state first and skips
anything already done. Nothing is committed — you review with `git diff`
and commit yourself.

What it does
  1. Deletes the build artefacts and leftover .patch files from the root
  2. Normalises CRLF line endings to LF and adds .gitattributes
  3. Adds the missing newline at end of file to every .py (ruff W292)
  4. Sorts three import blocks (ruff I001)
  5. Creates .dockerignore, pyproject.toml, LICENSE and README.md
  6. Extends .gitignore, and aligns the CI Python version with production
"""

from __future__ import annotations

import sys
from pathlib import Path

# ── Check these two before running ──────────────────────────────────────────
COPYRIGHT_HOLDER = "Hamidreza Soltanshah"
GITHUB_REPO = "whamidrezaw/time_manager_pro"

ROOT = Path(__file__).resolve().parent
report: list[str] = []


def log(status: str, message: str) -> None:
    report.append(f"  {status:<9} {message}")


# ── 1. Remove junk from the repository root ─────────────────────────────────

JUNK = [
    "Time Manager pro.zip",
    ".github.rar",
    "add-reminder-inline-keyboard.patch",
    "fix-auth-regressions.patch",
    "fix-hmac-log-token-leak.patch",
    "improve-reminder-reliability.patch",
    "my_changes.patch",
    "verified-fixes.patch",
    "critical-fixes.patch",
]


def remove_junk() -> None:
    for name in JUNK:
        path = ROOT / name
        if path.exists():
            path.unlink()
            log("deleted", name)
        else:
            log("skipped", f"{name} (not present)")


# ── 2. Line endings ─────────────────────────────────────────────────────────

TEXT_SUFFIXES = {
    ".py", ".js", ".css", ".html", ".md", ".txt", ".yml", ".yaml",
    ".toml", ".ini", ".sh", ".cfg", ".json", ".service", ".example",
}
SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", ".ruff_cache", ".pytest_cache"}


def iter_text_files():
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix in TEXT_SUFFIXES or path.name in {".gitignore", ".env.example"}:
            yield path


def normalize_line_endings() -> None:
    changed = 0
    for path in iter_text_files():
        raw = path.read_bytes()
        if b"\r\n" not in raw:
            continue
        path.write_bytes(raw.replace(b"\r\n", b"\n"))
        log("normalised", str(path.relative_to(ROOT)))
        changed += 1
    if changed == 0:
        log("skipped", "line endings already LF everywhere")


# ── 3. ruff W292 — newline at end of file ───────────────────────────────────

def fix_missing_final_newline() -> None:
    changed = 0
    for path in ROOT.rglob("*.py"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        raw = path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            path.write_bytes(raw + b"\n")
            log("newline", str(path.relative_to(ROOT)))
            changed += 1
    if changed == 0:
        log("skipped", "every .py already ends with a newline")


# ── 4. ruff I001 — import ordering ──────────────────────────────────────────

IMPORT_FIXES: list[tuple[str, str, str]] = [
    (
        "app/services/reminders.py",
        "from app.utils.dates import calc_next_notify, expire_for_repeat, "
        "repeat_label, safe_zoneinfo, to_jalali\n",
        "from app.utils.dates import (\n"
        "    calc_next_notify,\n"
        "    expire_for_repeat,\n"
        "    repeat_label,\n"
        "    safe_zoneinfo,\n"
        "    to_jalali,\n"
        ")\n",
    ),
    (
        "tests/conftest.py",
        "from httpx import ASGITransport, AsyncClient\n\n\n# \u2500\u2500\u2500",
        "from httpx import ASGITransport, AsyncClient\n\n# \u2500\u2500\u2500",
    ),
    (
        # One atomic move, not two edits: an "insert here, delete there" pair
        # would re-apply its insert half on a second run and duplicate the
        # import, because the deleted half is gone by then.
        "tests/test_auth.py",
        "from app.services.auth import (\n"
        "    build_data_check_string,\n"
        "    check_rate_limit,\n"
        "    compute_telegram_hash,\n"
        "    parse_init_data,\n"
        "    parse_init_user,\n"
        "    validate_auth_date,\n"
        "    validate_init_data,\n"
        ")\n"
        "from app.config import get_settings\n",
        "from app.config import get_settings\n"
        "from app.services.auth import (\n"
        "    build_data_check_string,\n"
        "    check_rate_limit,\n"
        "    compute_telegram_hash,\n"
        "    parse_init_data,\n"
        "    parse_init_user,\n"
        "    validate_auth_date,\n"
        "    validate_init_data,\n"
        ")\n",
    ),
]


def fix_import_order() -> None:
    for rel, old, new in IMPORT_FIXES:
        path = ROOT / rel
        if not path.exists():
            log("MISSING", f"{rel} — skipped")
            continue
        text = path.read_text(encoding="utf-8")
        if new in text and old not in text:
            log("skipped", f"{rel} imports (already sorted)")
            continue
        if text.count(old) != 1:
            log("MANUAL", f"{rel} imports — no unique match, sort by hand")
            continue
        path.write_text(text.replace(old, new, 1), encoding="utf-8")
        log("sorted", f"{rel} imports")


# ── 5. New files ────────────────────────────────────────────────────────────

GITATTRIBUTES = """\
# One line ending for everyone. Without this, editing a file on Windows and
# on the GitHub web UI produces diffs where every single line looks changed.
* text=auto eol=lf

*.png binary
*.ico binary
*.zip binary
"""

DOCKERIGNORE = """\
# The Dockerfile does `COPY . .`, so anything not listed here ends up in the
# image. Keeps the worker image small and free of local secrets.
.git
.github
.gitignore
.gitattributes
.dockerignore

__pycache__/
*.py[cod]
.pytest_cache/
.ruff_cache/
.coverage
htmlcov/

.venv/
venv/
env/

.env
.env.*
!.env.example

.vscode/
.idea/
.DS_Store

tests/
deploy/
README.md
LICENSE
fix_batch2.py

*.zip
*.rar
*.patch
*.log
"""

PYPROJECT = """\
# Pins the lint configuration so a new ruff release cannot change what CI
# accepts. The rule set is deliberately conservative: it matches what the
# code already passes today. Tightening it (I, UP, B, SIM) is a separate,
# reviewable change rather than a surprise red build.

[tool.ruff]
line-length = 110
target-version = "py312"
exclude = [".venv", "venv", "__pycache__", ".ruff_cache", ".pytest_cache"]

[tool.ruff.lint]
select = ["E", "F", "W", "I"]

[tool.ruff.lint.isort]
known-first-party = ["app", "worker", "tests"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
# Silences the pytest-asyncio deprecation warning by stating the scope it
# will default to in a future release, rather than waiting to be surprised.
asyncio_default_fixture_loop_scope = "function"
testpaths = ["tests"]
python_files = ["test_*.py"]
python_functions = ["test_*"]
addopts = "-q"
"""

LICENSE = """\
MIT License

Copyright (c) 2026 {holder}

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

README = """\
# TimeManager Pro

A Telegram Mini App and bot that remembers dates for you, on the Gregorian and
the Jalali calendar at the same time. Add a birthday, a meeting or an
appointment in the app, and the reminder arrives as a Telegram message with a
one-tap snooze.

[![CI](https://github.com/{repo}/actions/workflows/ci.yml/badge.svg)](https://github.com/{repo}/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.12-blue)
![License](https://img.shields.io/badge/license-MIT-green)

## Why it exists

Calendar apps assume you live in one calendar system. If half of the dates
that matter to you are Jalali and the other half Gregorian, you end up
converting them in your head or keeping two lists. TimeManager Pro stores one
event and shows you both dates, then reaches you where you already are: in
Telegram, with no extra app to install and no account to create.

## Features

- **Dual calendar.** Every event carries both its Gregorian and its Jalali date.
- **Reminders in Telegram.** A message arrives at the hour and minute you chose,
  in your own timezone, with a *Snooze 1h* button and a deep link back to the event.
- **Recurring events.** Daily, weekly, monthly and yearly, with an optional end
  date. Month-end dates roll correctly through short months, and 29 February
  is handled on non-leap years.
- **Categories, pinning and notes.** Nine categories, pinned events at the top,
  and a 2000-character note on any event.
- **Search and filters.** Filter by category or pinned status, or search across
  titles, notes and both date formats.
- **Follows your Telegram theme.** Colours, dark mode and the native date
  pickers all track the theme you picked in Telegram.

## Screenshots

> To do: add screenshots of the event list, the composer sheet and a reminder
> message. A short GIF of adding an event and receiving the reminder is the
> single highest-value addition to this page.

## How it works

```mermaid
flowchart LR
    U([Telegram user])
    W["FastAPI web<br/>/webapp + /api/*"]
    H["FastAPI webhook<br/>/telegram/webhook"]
    DB[("MongoDB")]
    R["Reminder worker"]

    U -->|opens Mini App| W
    U -->|/start, button taps| H
    W -->|initData HMAC check| DB
    H --> DB
    R -->|polls due events| DB
    R -->|sends the reminder| U
```

Requests from the Mini App carry Telegram's `initData` string. The server
recomputes its HMAC-SHA256 signature with the bot token, checks the timestamp
for freshness, and only then trusts the user id inside. Every read and write is
scoped to that id, so one user can never reach another user's events.

The reminder worker claims each due event by flipping its status to
`processing` before sending, and re-queues anything left processing for too
long. That makes a crash mid-send safe, and lets more than one worker run at
once without sending a reminder twice.

## Tech stack

| Layer | Choice |
|---|---|
| API | FastAPI, Pydantic v2, Uvicorn / Gunicorn |
| Database | MongoDB via Motor, with TTL and compound indexes |
| Bot | python-telegram-bot |
| Front end | Vanilla JavaScript, no build step, Jinja2 template |
| Dates | `zoneinfo` for timezones, `jdatetime` for the Jalali calendar |
| Quality | ruff, pytest, GitHub Actions |

## Getting started

Requirements: Python 3.12, a MongoDB instance (local or Atlas), and a bot
token from [@BotFather](https://t.me/BotFather).

```bash
git clone https://github.com/{repo}.git
cd time_manager_pro

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt

cp .env.example .env               # then fill in the real values
```

Run the web app and the worker in two terminals:

```bash
./deploy/run-dev.sh                # uvicorn on http://127.0.0.1:8000
./deploy/run-worker.sh             # the reminder loop
```

The Mini App only runs inside Telegram, because it needs the `initData` that
Telegram injects. To try it locally, expose port 8000 with a tunnel, set
`WEBAPP_BASE_URL` to the tunnel URL, and point your bot's menu button there
in BotFather.

## Configuration

Everything is read from environment variables; see `.env.example` for the full
list with comments.

| Variable | Purpose |
|---|---|
| `BOT_TOKEN` | Bot token from BotFather |
| `TELEGRAM_WEBHOOK_SECRET` | Shared secret Telegram echoes back on every webhook call |
| `MONGO_URI`, `MONGO_DB_NAME` | Database connection |
| `WEBAPP_BASE_URL` | Public base URL; the webhook is registered against it at startup |
| `TELEGRAM_BOT_USERNAME`, `TELEGRAM_MINI_APP_SHORT_NAME` | Used to build deep links |
| `RATE_LIMIT_COUNT` | Requests allowed per user per minute |
| `MAX_EVENTS_PER_USER`, `MAX_TITLE_LEN`, `MAX_NOTE_LEN` | Per-user limits |
| `REMINDER_POLL_INTERVAL_SECS`, `REMINDER_BATCH_SIZE`, `STALE_PROCESSING_SECS` | Worker tuning |

## Tests

```bash
ruff check .
pytest
```

The suite covers the HMAC verification path, timestamp validation, rate
limiting, date and recurrence maths, the event API, and the webhook.

## Deployment

The `Dockerfile` builds the reminder worker; `fly.toml` deploys it to Fly.io.
`.github/workflows/reminder.yml` runs the same job on a schedule as a
fallback, with a healthchecks.io dead-man's switch. `deploy/systemd/` holds
unit files for a plain VPS.

## Project structure

```
app/
  main.py            FastAPI app, lifespan, webhook registration
  config.py          Settings, validated at startup
  db.py              Mongo connection and index setup
  routes/            web, events API, telegram webhook, health
  services/          auth (initData + rate limit), events, reminders
  schemas/           request and response models
  utils/             timezone, Jalali and recurrence helpers
worker/              the reminder loop and a one-shot runner
static/, templates/  the Mini App
tests/
```

## Roadmap

- Times of day on events, not only dates
- Server-side search and filtering
- Persian interface with full RTL support
- A real Jalali date picker
- Month and agenda views
- `/today` and `/week` commands, and a morning digest

## License

MIT, see [LICENSE](LICENSE).
"""


def create_files() -> None:
    new_files = {
        ".gitattributes": GITATTRIBUTES,
        ".dockerignore": DOCKERIGNORE,
        "pyproject.toml": PYPROJECT,
        "LICENSE": LICENSE.format(holder=COPYRIGHT_HOLDER),
        "README.md": README.format(repo=GITHUB_REPO),
    }
    for name, content in new_files.items():
        path = ROOT / name
        if path.exists() and path.read_text(encoding="utf-8") == content:
            log("skipped", f"{name} (already up to date)")
            continue
        verb = "rewrote" if path.exists() else "created"
        path.write_text(content, encoding="utf-8")
        log(verb, name)


# ── 6. Edits to existing config ─────────────────────────────────────────────

def drop_pytest_ini() -> None:
    """pytest.ini takes precedence over pyproject.toml, so leaving both means
    the pyproject section is dead config that silently does nothing."""
    path = ROOT / "pytest.ini"
    if not path.exists():
        log("skipped", "pytest.ini (already removed)")
        return
    path.unlink()
    log("deleted", "pytest.ini (config moved into pyproject.toml)")


def extend_gitignore() -> None:
    path = ROOT / ".gitignore"
    if not path.exists():
        log("MISSING", ".gitignore")
        return
    text = path.read_text(encoding="utf-8")
    if "# Archives" in text:
        log("skipped", ".gitignore (already extended)")
        return
    path.write_text(
        text.rstrip("\n") + "\n\n# Archives — these do not belong in a source repo\n*.zip\n*.rar\n*.7z\n",
        encoding="utf-8",
    )
    log("extended", ".gitignore")


def align_ci_python() -> None:
    """CI tested on 3.11 while the Dockerfile and the reminder workflow run
    3.12. Testing on a different minor than production is how a subtle
    stdlib behaviour change reaches users before a test ever sees it."""
    path = ROOT / ".github" / "workflows" / "ci.yml"
    if not path.exists():
        log("MISSING", ".github/workflows/ci.yml")
        return
    text = path.read_text(encoding="utf-8")
    if 'python-version: "3.12"' in text:
        log("skipped", "ci.yml (already on 3.12)")
        return
    if "3.11.11" not in text:
        log("MANUAL", "ci.yml — unexpected Python version, check by hand")
        return
    text = text.replace("Set up Python 3.11", "Set up Python 3.12")
    text = text.replace('python-version: "3.11.11"', 'python-version: "3.12"')
    path.write_text(text, encoding="utf-8")
    log("updated", "ci.yml (Python 3.11 -> 3.12, matching production)")


# ── Runner ──────────────────────────────────────────────────────────────────

def main() -> int:
    if not (ROOT / "app" / "main.py").exists():
        print("Run this from the repository root: app/main.py was not found.")
        return 1

    steps = [
        ("Removing build artefacts and stray patches", remove_junk),
        ("Normalising line endings to LF", normalize_line_endings),
        ("Adding missing final newlines", fix_missing_final_newline),
        ("Sorting import blocks", fix_import_order),
        ("Creating README, LICENSE and config files", create_files),
        ("Consolidating pytest config", drop_pytest_ini),
        ("Extending .gitignore", extend_gitignore),
        ("Aligning the CI Python version", align_ci_python),
    ]

    for title, func in steps:
        report.append(f"\n{title}")
        func()

    print("\n".join(report))
    print(
        "\nDone. Next:\n"
        "  ruff check . && pytest -q\n"
        "  git add -A && git rm --cached fix_batch2.py 2>/dev/null\n"
        '  git commit -m "Clean up repo root, add README, LICENSE and lint config"\n'
    )
    if any("MANUAL" in line or "MISSING" in line for line in report):
        print("Some steps need a look by hand — search the output above for MANUAL or MISSING.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
