#!/usr/bin/env python3
"""
fix_batch7.py — TimeManager Pro, batch 7: a real date picker, no typing.

Run once from the repository root:

    pip install -r requirements.txt
    python fix_batch7.py
    python -m ruff check . ; python -m pytest -q

What changes

  All three date fields — Gregorian, Jalali, and repeat-until — become
  read-only and open a picker instead. Three scroll-snapping columns for year,
  month and day, with a tab to switch calendar. Whatever settles under the
  highlight band is the value, so there is no drag maths to get wrong and
  momentum scrolling comes free from the browser.

  Switching the calendar tab converts the current selection rather than
  resetting it, and the line under the wheels always shows the date in both
  calendars at once, so there is never a question of which one is being edited.

  The day column is rebuilt whenever the month or year moves, because its
  length depends on both: February, and Esfand in a Jalali leap year. Picking
  30 Esfand and then moving to a common year clamps to 29 rather than producing
  a date that does not exist.

Why the ranges are what they are

  The server rejects any year outside 1900–2200. The Jalali range is derived
  from that bound rather than guessed, so every date the wheels can produce is
  one the API will accept. That was checked exhaustively: all 7,200 first and
  last days of every Jalali month in the offered range convert inside the
  window and round-trip back unchanged.

Fitting into what is already there

  The picker sits at z-index 50 — above the composer sheet at 41, below the
  confirm dialog at 60 — and Escape and the Telegram back button close it
  before they close anything underneath. In right-to-left the wheel row keeps
  its Year | Month | Day order, since each column is a list of numbers rather
  than a sentence and reversing it would only be disorienting.

  This rests on the converter rewritten in batch 6. The old one threw on every
  real date, so a picker built on it would have produced nothing at all.

Safe to run twice.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
report: list[str] = []


def log(status: str, message: str) -> None:
    report.append(f"  {status:<9} {message}")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


CONTENT_1 = r'''<!DOCTYPE html>
<html lang="en" dir="ltr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
  <meta name="theme-color" content="#5b6cf8">
  <meta name="color-scheme" content="light dark">
  <meta name="description" content="TimeManager Pro — Smart event reminders with Gregorian and Jalali dates for Telegram.">
  <title>TimeManager Pro</title>
  <!-- Self-hosted: Google Fonts is slow or unreachable for a large part of this
       app's audience, and Plus Jakarta Sans carries no Persian glyphs at all,
       so every Persian word fell back to whatever the device happened to have. -->
  <link rel="preload" href="/static/fonts/Vazirmatn-Regular.woff2?v={{ asset_version }}" as="font" type="font/woff2" crossorigin>
  <link rel="stylesheet" href="/static/style.css?v={{ asset_version }}">
  <script src="https://telegram.org/js/telegram-web-app.js"></script>
</head>
<body>
  <a href="#eventList" class="sr-only">Skip to content</a>

  <noscript>
    <div class="noscript-box">
      <div class="noscript-icon">📅</div>
      <p><strong>JavaScript Required</strong></p>
      <p class="noscript-sub">Please enable JavaScript to use TimeManager Pro.</p>
    </div>
  </noscript>

  <!-- ── Header ─────────────────────────────────────── -->
  <header class="app-header">
    <div class="brand">
      <div class="brand-mark" aria-hidden="true">
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
          <rect x="3" y="4" width="18" height="17" rx="3" stroke="currentColor" stroke-width="2"/>
          <path d="M3 9h18" stroke="currentColor" stroke-width="2"/>
          <path d="M8 2v4M16 2v4" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
          <circle cx="12" cy="15" r="2" fill="currentColor"/>
        </svg>
      </div>
      <div class="brand-copy">
        <strong class="brand-title">TimeManager Pro</strong>
        <span class="brand-subtitle">Smart reminders in Telegram</span>
      </div>
    </div>
    <button type="button" id="refreshBtn" class="icon-btn" aria-label="Refresh events" title="Refresh">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M23 4v6h-6"/><path d="M1 20v-6h6"/>
        <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>
      </svg>
    </button>
  </header>

  <!-- ── Main Content ───────────────────────────────── -->
  <main id="eventList" class="app-main">

    <!-- Hero Card -->
    <section class="hero-card">
      <div class="hero-left">
        <div class="hero-badge">✨ Your Personal Planner</div>
        <h1 class="hero-title">Stay on top of every moment</h1>
        <p class="hero-text">Save events, birthdays & tasks — get reminders directly in Telegram.</p>
      </div>
      <div class="hero-stats">
        <div class="stat-box">
          <span class="stat-icon">📅</span>
          <strong id="eventCount" class="stat-value">0</strong>
          <span class="stat-label">Events</span>
        </div>
        <div class="stat-box">
          <span class="stat-icon">🔔</span>
          <strong id="syncStatus" class="stat-value">Ready</strong>
          <span class="stat-label">Status</span>
        </div>
      </div>
    </section>

    <!-- Toolbar -->
    <section class="toolbar" aria-label="Filters and search">
      <div class="toolbar-row" id="filterRow" role="group" aria-label="Category filter">
        <button type="button" class="seg-btn is-active" data-filter="all">🌐 All</button>
        <button type="button" class="seg-btn" data-filter="pinned">📌 Pinned</button>
        <button type="button" class="seg-btn" data-filter="birthday">🎂 Birthday</button>
        <button type="button" class="seg-btn" data-filter="work">💼 Work</button>
        <button type="button" class="seg-btn" data-filter="health">❤️ Health</button>
        <button type="button" class="seg-btn" data-filter="family">👨‍👩‍👧 Family</button>
        <button type="button" class="seg-btn" data-filter="travel">✈️ Travel</button>
        <button type="button" class="seg-btn" data-filter="finance">💰 Finance</button>
        <button type="button" class="seg-btn" data-filter="study">📚 Study</button>
        <button type="button" class="seg-btn" data-filter="past">🗄️ Past</button>
      </div>
      <label class="search-wrap" for="searchInput">
        <svg class="search-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/>
        </svg>
        <input type="search" id="searchInput" class="search-input" placeholder="Search events…" autocomplete="off" inputmode="search">
      </label>
    </section>

    <!-- Skeleton (hidden by default, shown while loading) -->
    <section id="skeletonState" class="skeleton-list" hidden>
      <div class="skeleton-card"><div class="sk-line sk-title"></div><div class="sk-line sk-sub"></div><div class="sk-line sk-sub short"></div></div>
      <div class="skeleton-card"><div class="sk-line sk-title"></div><div class="sk-line sk-sub"></div><div class="sk-line sk-sub short"></div></div>
      <div class="skeleton-card"><div class="sk-line sk-title"></div><div class="sk-line sk-sub"></div></div>
    </section>

    <!-- Empty State -->
    <section id="listState" class="empty-state" hidden>
      <div class="empty-illustration" aria-hidden="true">
        <div class="empty-circle">
          <span class="empty-emoji">🗓️</span>
        </div>
        <div class="empty-dots">
          <span></span><span></span><span></span>
        </div>
      </div>
      <h2 class="empty-state-title">No events yet!</h2>
      <p class="empty-state-text">Add your first event and start receiving smart reminders directly in Telegram.</p>
      <button type="button" id="emptyAddBtn" class="btn-primary btn-cta">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>
        Add your first event
      </button>
      <div class="empty-hints">
        <div class="hint-chip">🎂 Birthdays</div>
        <div class="hint-chip">💼 Meetings</div>
        <div class="hint-chip">❤️ Appointments</div>
        <div class="hint-chip">✈️ Travel</div>
      </div>
    </section>

    <!-- Error State -->
    <section id="listErrorState" class="empty-state is-error" hidden>
      <div class="empty-illustration" aria-hidden="true">
        <div class="empty-circle is-error-circle"><span class="empty-emoji">⚠️</span></div>
      </div>
      <h2 class="empty-state-title">Something went wrong</h2>
      <p class="empty-state-text">Could not connect to the server. Please check your connection and try again.</p>
      <button type="button" id="retryBtn" class="btn-primary">Try again</button>
    </section>

    <!-- No Results State -->
    <section id="noResultsState" class="empty-state" hidden>
      <div class="empty-illustration" aria-hidden="true">
        <div class="empty-circle"><span class="empty-emoji">🔍</span></div>
      </div>
      <h2 class="empty-state-title">No results found</h2>
      <p class="empty-state-text">Try a different search term or filter.</p>
    </section>

    <!-- Events List -->
    <section id="eventsWrap" class="events-wrap" aria-live="polite" aria-label="Event list"></section>

    <!-- Load More -->
    <div id="loadMoreWrap" class="load-more-wrap" hidden>
      <button type="button" id="loadMoreBtn" class="btn-secondary btn-load-more">Load more events</button>
    </div>
  </main>

  <!-- ── Floating Add Button ────────────────────────── -->
  <button type="button" id="openComposerBtn" class="floating-add-btn" aria-label="Add event" aria-controls="composerSheet" aria-expanded="false">
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>
    <span>Add Event</span>
  </button>

  <!-- ── Sheet Overlay ──────────────────────────────── -->
  <div id="sheetOverlay" class="sheet-overlay" hidden aria-hidden="true"></div>

  <!-- ── Composer Sheet ─────────────────────────────── -->
  <section id="composerSheet" class="sheet" role="dialog" aria-modal="true" aria-labelledby="composerTitle" aria-hidden="true" hidden>
    <div class="sheet-handle" aria-hidden="true"></div>
    <div class="sheet-head">
      <div>
        <h2 id="composerTitle" class="sheet-title">New Event</h2>
        <p id="composerSubtitle" class="sheet-subtitle">Set title, date and repeat pattern.</p>
      </div>
      <button type="button" id="closeComposerX" class="icon-btn" aria-label="Close form">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M18 6 6 18M6 6l12 12"/></svg>
      </button>
    </div>

    <form id="eventForm" class="sheet-form" novalidate>
      <input type="hidden" id="eventId">

      <div class="field-group">
        <label class="field-label" for="title">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
          Event Title
        </label>
        <input type="text" id="title" class="field-input" placeholder="e.g. Mom's Birthday" maxlength="200" autocomplete="off" required>
      </div>

      <div class="grid-2">
        <div class="field-group">
          <label class="field-label" for="date">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/></svg>
            Gregorian Date
          </label>
          <input type="text" id="date" class="field-input field-picker" required
                 readonly inputmode="none" autocomplete="off" placeholder="2026-04-20">
        </div>
        <div class="field-group">
          <label class="field-label" for="date-jalali">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="12" cy="12" r="10"/><path d="M12 8v4l3 3"/></svg>
            Jalali Date
          </label>
          <input type="text" id="date-jalali" class="field-input field-picker" placeholder="1405/01/31"
                 readonly inputmode="none" autocomplete="off" dir="ltr">
        </div>
      </div>

      <div class="grid-2">
        <div class="field-group">
          <label class="field-label" for="repeat">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M17 1l4 4-4 4"/><path d="M3 11V9a4 4 0 0 1 4-4h14"/><path d="M7 23l-4-4 4-4"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/></svg>
            Repeat
          </label>
          <select id="repeat" class="field-input field-select">
            <option value="none">One time</option>
            <option value="daily">Daily</option>
            <option value="weekly">Weekly</option>
            <option value="monthly">Monthly</option>
            <option value="yearly">Yearly</option>
          </select>
        </div>
        <div class="field-group" id="repeatUntilWrap" hidden>
          <label class="field-label" for="repeatUntil">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>
            Repeat Until
            <span class="field-optional">(optional)</span>
          </label>
          <input type="text" id="repeatUntil" class="field-input field-picker"
                 readonly inputmode="none" autocomplete="off" placeholder="2026-12-31">
        </div>
        <div class="field-group">
          <label class="field-label" for="category">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"/><line x1="7" y1="7" x2="7.01" y2="7"/></svg>
            Category
          </label>
          <select id="category" class="field-input field-select">
            <option value="general">🌐 General</option>
            <option value="birthday">🎂 Birthday</option>
            <option value="work">💼 Work</option>
            <option value="family">👨‍👩‍👧 Family</option>
            <option value="health">❤️ Health</option>
            <option value="travel">✈️ Travel</option>
            <option value="finance">💰 Finance</option>
            <option value="study">📚 Study</option>
            <option value="other">📌 Other</option>
          </select>
        </div>
      </div>

      <label class="check-row" for="allDay">
        <input type="checkbox" id="allDay" checked>
        <span class="check-label">
          <span class="check-icon">📆</span>
          All-day event
        </span>
      </label>

      <div class="grid-2">
        <div class="field-group" id="eventTimeWrap" hidden>
          <label class="field-label" for="eventTime">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg>
            Event Time
          </label>
          <input type="time" id="eventTime" class="field-input" value="09:00">
        </div>
        <div class="field-group" id="reminderTimeWrap">
          <label class="field-label" for="reminderTime">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>
            Reminder Time
          </label>
          <input type="time" id="reminderTime" class="field-input" value="09:00">
        </div>
        <div class="field-group" id="reminderOffsetWrap" hidden>
          <label class="field-label" for="reminderOffset">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>
            Remind Me
          </label>
          <select id="reminderOffset" class="field-input field-select">
            <option value="0">At time of event</option>
            <option value="15">15 minutes before</option>
            <option value="30">30 minutes before</option>
            <option value="60" selected>1 hour before</option>
            <option value="120">2 hours before</option>
            <option value="1440">1 day before</option>
            <option value="10080">1 week before</option>
          </select>
        </div>
      </div>

      <label class="check-row" for="pin">
        <input type="checkbox" id="pin">
        <span class="check-label">
          <span class="check-icon">📌</span>
          Pin this event to the top
        </span>
      </label>

      <div class="field-group">
        <label class="field-label" for="note">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/></svg>
          Note <span class="field-optional">(optional)</span>
        </label>
        <textarea id="note" class="field-input field-textarea" rows="4" maxlength="2000" placeholder="Add details, tasks, or a checklist…"></textarea>
        <span class="char-count" id="noteCharCount">0 / 2000</span>
      </div>

      <div class="form-actions">
        <button type="button" id="cancelBtn" class="btn-secondary">Cancel</button>
        <button type="submit" id="saveEventBtn" class="btn-primary">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" aria-hidden="true"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg>
          Save Event
        </button>
      </div>
    </form>
  </section>

  <!-- ── Detail Sheet ───────────────────────────────── -->
  <section id="detailSheet" class="sheet" role="dialog" aria-modal="true" aria-labelledby="detailTitle" aria-hidden="true" hidden>
    <div class="sheet-handle" aria-hidden="true"></div>
    <div class="sheet-head">
      <div>
        <h2 id="detailTitle" class="sheet-title">Event Details</h2>
        <p id="detailSubtitle" class="sheet-subtitle">Full view and actions</p>
      </div>
      <button type="button" id="closeDetailX" class="icon-btn" aria-label="Close details">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M18 6 6 18M6 6l12 12"/></svg>
      </button>
    </div>

    <article class="detail-card">
      <div class="detail-topline">
        <span id="detailCategoryBadge" class="badge">General</span>
        <span id="detailRepeatBadge" class="badge badge-muted">One time</span>
        <span id="detailPinnedBadge" class="badge badge-pin" hidden>📌 Pinned</span>
      </div>

      <h3 id="detailEventTitle" class="detail-event-title">—</h3>

      <!-- Countdown Ring -->
      <div class="countdown-ring-wrap" id="countdownRingWrap">
        <div class="countdown-ring" id="countdownRing">
          <span id="countdownDays" class="ring-days">—</span>
          <span class="ring-label">days</span>
        </div>
        <div id="detailCountdownText" class="countdown-ring-text">—</div>
      </div>

      <div class="detail-meta-grid">
        <div class="detail-meta-box">
          <span class="detail-meta-label">📅 Gregorian</span>
          <strong id="detailDateIso">—</strong>
        </div>
        <div class="detail-meta-box">
          <span class="detail-meta-label">🗓️ Jalali</span>
          <strong id="detailDateJalali">—</strong>
        </div>
        <div class="detail-meta-box">
          <span class="detail-meta-label">🌍 Timezone</span>
          <strong id="detailTimezone">—</strong>
        </div>
        <div class="detail-meta-box">
          <span class="detail-meta-label">🔔 Status</span>
          <strong id="detailStatus">—</strong>
        </div>
      </div>

      <div class="detail-actions">
        <button type="button" id="detailEditBtn" class="btn-action btn-action-edit">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
          Edit
        </button>
        <button type="button" id="detailShareBtn" class="btn-action btn-action-share">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><line x1="8.59" y1="13.51" x2="15.42" y2="17.49"/><line x1="15.41" y1="6.51" x2="8.59" y2="10.49"/></svg>
          Share
        </button>
        <button type="button" id="detailPinBtn" class="btn-action btn-action-pin">📌 Pin</button>
        <button type="button" id="detailDeleteBtn" class="btn-action btn-action-delete">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/></svg>
          Delete
        </button>
      </div>

      <div class="field-group">
        <label class="field-label" for="detailNote">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
          Event Note
        </label>
        <textarea id="detailNote" class="field-input field-textarea" rows="6" maxlength="2000" placeholder="Write a note, checklist, or details…"></textarea>
      </div>

      <div class="form-actions">
        <button type="button" id="detailNoteCancelBtn" class="btn-secondary">Reset</button>
        <button type="button" id="detailNoteSaveBtn" class="btn-primary">Save Note</button>
      </div>
    </article>
  </section>

  <!-- ── First-run Onboarding ──────────────────────── -->
  <div id="onboardingOverlay" class="confirm-overlay onboarding-overlay" hidden aria-hidden="true">
    <div class="confirm-dialog onboarding-dialog" role="dialog" aria-modal="true" aria-labelledby="onboardingTitle">
      <div class="confirm-icon" id="onboardingIcon">🗓️</div>
      <h3 id="onboardingTitle" class="confirm-title">Never miss what matters</h3>
      <p id="onboardingText" class="confirm-text">Add birthdays, appointments, and anything else you want to remember — TimeManager Pro keeps track so you don't have to.</p>
      <div class="onboarding-dots" id="onboardingDots" aria-hidden="true">
        <span class="is-active"></span><span></span><span></span>
      </div>
      <div class="confirm-actions onboarding-actions">
        <button type="button" id="onboardingSkipBtn" class="btn-secondary">Skip</button>
        <button type="button" id="onboardingNextBtn" class="btn-primary">Next</button>
      </div>
    </div>
  </div>

  <!-- ── Custom Confirm Dialog ──────────────────────── -->
  <!-- Date picker. Its own overlay rather than a sheet, because it opens on
       top of the composer sheet that contains the fields. -->
  <div id="dpOverlay" class="dp-overlay" hidden aria-hidden="true">
    <div class="dp-dialog" role="dialog" aria-modal="true" aria-labelledby="dpTitle">
      <div class="sheet-handle" aria-hidden="true"></div>

      <div class="dp-head">
        <h2 class="sheet-title" id="dpTitle">Pick a date</h2>
        <div class="dp-tabs" role="tablist" aria-label="Calendar">
          <button type="button" class="dp-tab is-active" data-calendar="gregorian" role="tab" aria-selected="true">Gregorian</button>
          <button type="button" class="dp-tab" data-calendar="jalali" role="tab" aria-selected="false">Jalali</button>
        </div>
      </div>

      <div class="dp-wheels" id="dpWheels">
        <div class="dp-wheel" id="dpYear"  role="listbox" aria-label="Year" tabindex="0"></div>
        <div class="dp-wheel" id="dpMonth" role="listbox" aria-label="Month" tabindex="0"></div>
        <div class="dp-wheel" id="dpDay"   role="listbox" aria-label="Day" tabindex="0"></div>
        <div class="dp-highlight" aria-hidden="true"></div>
      </div>

      <p class="dp-preview" id="dpPreview" aria-live="polite"></p>

      <div class="dp-actions">
        <button type="button" class="btn-secondary" id="dpToday">Today</button>
        <button type="button" class="btn-secondary" id="dpClear" hidden>Clear</button>
        <button type="button" class="btn-secondary" id="dpCancel">Cancel</button>
        <button type="button" class="btn-primary" id="dpConfirm">Confirm</button>
      </div>
    </div>
  </div>

  <div id="confirmOverlay" class="confirm-overlay" hidden aria-hidden="true">
    <div class="confirm-dialog" role="alertdialog" aria-modal="true" aria-labelledby="confirmTitle">
      <div class="confirm-icon" id="confirmIcon">🗑️</div>
      <h3 id="confirmTitle" class="confirm-title">Delete Event?</h3>
      <p id="confirmText" class="confirm-text">This action cannot be undone.</p>
      <div class="confirm-actions">
        <button type="button" id="confirmCancelBtn" class="btn-secondary">Cancel</button>
        <button type="button" id="confirmOkBtn" class="btn-danger">Delete</button>
      </div>
    </div>
  </div>

  <!-- ── Toast ─────────────────────────────────────── -->
  <div id="toast" class="toast" role="status" aria-live="polite" aria-atomic="true"></div>

  <script src="/static/app.js?v={{ asset_version }}" defer></script>
</body>
</html>
'''

CONTENT_2 = r'''/**
 * TimeManager Pro — app.js v2.0
 * Fixes applied:
 *  - event_id (was: eventid) in edit/delete/pin/note payloads
 *  - All countdown text translated to English (was: Persian)
 *  - window.confirm replaced with custom confirm dialog
 *  - All debug console.log removed
 *  - API field names: date_iso / date_jalali / notify_status / tz_name
 *  - Pinned badge text: "Pinned" (was: "سنجاق‌شده")
 *  - Skeleton loading state
 *  - Pagination / load-more
 *  - Countdown ring in detail view
 *  - Note character counter
 *  - Reminder hour field support
 *  - Reminder hour actually sent to the backend (was: silently dropped)
 *  - Haptic feedback on save/delete/pin/error (via Telegram WebApp SDK)
 *  - First-run onboarding overlay (3 steps, shown once via localStorage)
 */

(() => {
  "use strict";

  /* ── Telegram WebApp ────────────────────────────────── */
  const tg = window.Telegram?.WebApp || null;

  function fatal(message) {
    document.body.innerHTML = `
      <div style="padding:40px 20px;text-align:center;font-family:system-ui,sans-serif;">
        <div style="font-size:2.5rem;margin-bottom:16px;">⚠️</div>
        <h2 style="margin:0 0 12px;font-size:1.2rem;">Something went wrong</h2>
        <p style="color:#666;margin:0;">${String(message).replace(/</g, "&lt;")}</p>
      </div>
    `;
  }

  if (!tg) {
    fatal("This application only works inside Telegram. Please open it via the Telegram Mini App.");
    return;
  }

  try { tg.ready(); tg.expand(); } catch (_) {}

  const initData = tg.initData || "";

  if (!initData) {
    fatal("Telegram Mini App could not authenticate. Please reopen the app from Telegram.");
    return;
  }

  /* ── App State ──────────────────────────────────────── */
  const state = {
    events: [],
    filteredEvents: [],
    currentFilter: "all",
    searchTerm: "",
    activeSheet: null,
    detailEventId: null,
    editingEventId: null,
    lastFocusedElement: null,
    skip: 0,
    hasMore: false,
    isLoading: false,
    initData,
  };

  /* ── Element Refs ───────────────────────────────────── */
  const $ = (id) => document.getElementById(id);
  const $$ = (sel) => [...document.querySelectorAll(sel)];

  const els = {
    syncStatus:         $("syncStatus"),
    eventCount:         $("eventCount"),
    refreshBtn:         $("refreshBtn"),
    retryBtn:           $("retryBtn"),
    emptyAddBtn:        $("emptyAddBtn"),
    onboardingOverlay:  $("onboardingOverlay"),
    onboardingIcon:     $("onboardingIcon"),
    onboardingTitle:    $("onboardingTitle"),
    onboardingText:     $("onboardingText"),
    onboardingDots:     $("onboardingDots"),
    onboardingSkipBtn:  $("onboardingSkipBtn"),
    onboardingNextBtn:  $("onboardingNextBtn"),
    repeatUntilWrap:    $("repeatUntilWrap"),
    repeatUntil:        $("repeatUntil"),
    searchInput:        $("searchInput"),
    filterButtons:      $$("[data-filter]"),
    eventsWrap:         $("eventsWrap"),
    listState:          $("listState"),
    listErrorState:     $("listErrorState"),
    noResultsState:     $("noResultsState"),
    skeletonState:      $("skeletonState"),
    loadMoreWrap:       $("loadMoreWrap"),
    loadMoreBtn:        $("loadMoreBtn"),
    toast:              $("toast"),

    // Composer
    openComposerBtn:    $("openComposerBtn"),
    closeComposerX:     $("closeComposerX"),
    cancelBtn:          $("cancelBtn"),
    saveEventBtn:       $("saveEventBtn"),
    composerSheet:      $("composerSheet"),
    composerTitle:      $("composerTitle"),
    composerSubtitle:   $("composerSubtitle"),
    eventForm:          $("eventForm"),
    eventId:            $("eventId"),
    title:              $("title"),
    date:               $("date"),
    dateJalali:         $("date-jalali"),
    repeat:             $("repeat"),
    category:           $("category"),
    pin:                $("pin"),
    note:               $("note"),
    noteCharCount:      $("noteCharCount"),
    allDay:             $("allDay"),
    eventTimeWrap:      $("eventTimeWrap"),
    eventTime:          $("eventTime"),
    reminderTimeWrap:   $("reminderTimeWrap"),
    reminderTime:       $("reminderTime"),
    reminderOffsetWrap: $("reminderOffsetWrap"),
    reminderOffset:     $("reminderOffset"),
    dpOverlay:          $("dpOverlay"),
    dpTabs:             Array.from(document.querySelectorAll(".dp-tab")),
    dpYear:             $("dpYear"),
    dpMonth:            $("dpMonth"),
    dpDay:              $("dpDay"),
    dpPreview:          $("dpPreview"),
    dpToday:            $("dpToday"),
    dpClear:            $("dpClear"),
    dpCancel:           $("dpCancel"),
    dpConfirm:          $("dpConfirm"),

    // Detail
    detailSheet:        $("detailSheet"),
    closeDetailX:       $("closeDetailX"),
    detailEditBtn:      $("detailEditBtn"),
    detailShareBtn:     $("detailShareBtn"),
    detailPinBtn:       $("detailPinBtn"),
    detailDeleteBtn:    $("detailDeleteBtn"),
    detailNote:         $("detailNote"),
    detailNoteSaveBtn:  $("detailNoteSaveBtn"),
    detailNoteCancelBtn:$("detailNoteCancelBtn"),
    detailEventTitle:   $("detailEventTitle"),
    detailCategoryBadge:$("detailCategoryBadge"),
    detailRepeatBadge:  $("detailRepeatBadge"),
    detailPinnedBadge:  $("detailPinnedBadge"),
    detailDateIso:      $("detailDateIso"),
    detailDateJalali:   $("detailDateJalali"),
    detailTimezone:     $("detailTimezone"),
    detailStatus:       $("detailStatus"),
    countdownRing:      $("countdownRing"),
    countdownDays:      $("countdownDays"),
    detailCountdownText:$("detailCountdownText"),

    // Confirm dialog
    confirmOverlay:     $("confirmOverlay"),
    confirmTitle:       $("confirmTitle"),
    confirmText:        $("confirmText"),
    confirmOkBtn:       $("confirmOkBtn"),
    confirmCancelBtn:   $("confirmCancelBtn"),

    sheetOverlay:       $("sheetOverlay"),
  };

  /* ── Label Maps ─────────────────────────────────────── */
  const CATEGORY_LABELS = {
    general: "🌐 General",  birthday: "🎂 Birthday",
    work:    "💼 Work",      family:   "👨‍👩‍👧 Family",
    health:  "❤️ Health",   travel:   "✈️ Travel",
    finance: "💰 Finance",  study:    "📚 Study",
    other:   "📌 Other",
  };

  /* ── Language ────────────────────────────────────────── */
  // Keyed by the English source string rather than by an invented id: the
  // template needs no data-i18n attributes, so translating it is a DOM pass
  // instead of 99 markup edits, and any string with no entry simply stays
  // English. Only Persian is offered besides English — that is deliberate, not
  // a gap: Persian serves the audience the Jalali calendar is here for, and
  // English serves everyone else.
  const TRANSLATIONS = {
    fa: {
      // Shell
      "Skip to content": "پرش به محتوا",
      "JavaScript Required": "جاوااسکریپت لازم است",
      "Please enable JavaScript to use TimeManager Pro.": "برای استفاده از تایم‌منیجر پرو جاوااسکریپت را فعال کنید.",
      "Smart reminders in Telegram": "یادآوری هوشمند در تلگرام",
      "✨ Your Personal Planner": "✨ برنامه‌ریز شخصی شما",
      "Stay on top of every moment": "هیچ لحظه‌ای را از دست ندهید",
      "Save events, birthdays & tasks — get reminders directly in Telegram.": "رویدادها، تولدها و کارها را ذخیره کنید و یادآوری‌شان را در تلگرام بگیرید.",
      "Events": "رویداد",
      "Ready": "آماده",
      "Status": "وضعیت",
      "Refresh events": "بارگذاری دوباره",
      "Refresh": "بارگذاری دوباره",

      // Filters and search
      "Filters and search": "فیلتر و جست‌وجو",
      "Category filter": "فیلتر دسته",
      "Search events…": "جست‌وجوی رویداد…",
      "Event list": "فهرست رویدادها",
      "🌐 All": "🌐 همه",
      "📌 Pinned": "📌 سنجاق‌شده",
      "🎂 Birthday": "🎂 تولد",
      "💼 Work": "💼 کاری",
      "❤️ Health": "❤️ سلامت",
      "👨‍👩‍👧 Family": "👨‍👩‍👧 خانواده",
      "✈️ Travel": "✈️ سفر",
      "💰 Finance": "💰 مالی",
      "📚 Study": "📚 درسی",
      "🗄️ Past": "🗄️ گذشته",

      // Date picker
      "Pick a date": "انتخاب تاریخ",
      "Calendar": "تقویم",
      "Gregorian": "میلادی",
      "Jalali": "شمسی",
      "Year": "سال",
      "Month": "ماه",
      "Day": "روز",
      "Today": "امروز",
      "Clear": "پاک کردن",
      "Confirm": "تأیید",
      "January": "ژانویه", "February": "فوریه", "March": "مارس",
      "April": "آوریل", "May": "مه", "June": "ژوئن",
      "July": "ژوئیه", "August": "اوت", "September": "سپتامبر",
      "October": "اکتبر", "November": "نوامبر", "December": "دسامبر",
      "Farvardin": "فروردین", "Ordibehesht": "اردیبهشت", "Khordad": "خرداد",
      "Tir": "تیر", "Mordad": "مرداد", "Shahrivar": "شهریور",
      "Mehr": "مهر", "Aban": "آبان", "Azar": "آذر",
      "Dey": "دی", "Bahman": "بهمن", "Esfand": "اسفند",
      "🌐 General": "🌐 عمومی",
      "📌 Other": "📌 سایر",

      // Empty, error and result states
      "No events yet!": "هنوز رویدادی ندارید!",
      "Add your first event and start receiving smart reminders directly in Telegram.": "اولین رویدادتان را اضافه کنید و یادآوری‌ها را در تلگرام دریافت کنید.",
      "Add your first event": "افزودن اولین رویداد",
      "🎂 Birthdays": "🎂 تولدها",
      "💼 Meetings": "💼 جلسه‌ها",
      "❤️ Appointments": "❤️ قرارها",
      "✈️ Travel": "✈️ سفر",
      "Something went wrong": "مشکلی پیش آمد",
      "Could not connect to the server. Please check your connection and try again.": "اتصال به سرور ممکن نشد. اینترنت را بررسی و دوباره تلاش کنید.",
      "Try again": "تلاش دوباره",
      "No results found": "چیزی پیدا نشد",
      "Try a different search term or filter.": "عبارت یا فیلتر دیگری را امتحان کنید.",
      "Load more events": "رویدادهای بیشتر",

      // Composer
      "Add event": "افزودن رویداد",
      "Add Event": "افزودن رویداد",
      "New Event": "رویداد جدید",
      "Edit Event": "ویرایش رویداد",
      "Close form": "بستن فرم",
      "Set title, date and repeat pattern.": "عنوان، تاریخ و الگوی تکرار را مشخص کنید.",
      "Event Title": "عنوان رویداد",
      "e.g. Mom's Birthday": "مثلاً تولد مامان",
      "Gregorian Date": "تاریخ میلادی",
      "Jalali Date": "تاریخ شمسی",
      "Repeat": "تکرار",
      "One time": "یک‌بار",
      "Daily": "هر روز",
      "Weekly": "هر هفته",
      "Monthly": "هر ماه",
      "Yearly": "هر سال",
      "Repeat Until": "تکرار تا",
      "(optional)": "(اختیاری)",
      "Category": "دسته",
      "All-day event": "رویداد تمام‌روز",
      "Event Time": "ساعت رویداد",
      "Reminder Time": "ساعت یادآوری",
      "Remind Me": "یادآوری",
      "At time of event": "سر ساعت رویداد",
      "15 minutes before": "۱۵ دقیقه قبل",
      "30 minutes before": "۳۰ دقیقه قبل",
      "1 hour before": "۱ ساعت قبل",
      "2 hours before": "۲ ساعت قبل",
      "1 day before": "۱ روز قبل",
      "1 week before": "۱ هفته قبل",
      "Pin this event to the top": "این رویداد بالای فهرست بماند",
      "Note": "یادداشت",
      "Add details, tasks, or a checklist…": "جزئیات، کارها یا فهرست وارسی…",
      "Cancel": "انصراف",
      "Save Event": "ذخیره رویداد",
      "Save Changes": "ذخیره تغییرات",

      // Detail sheet
      "Event Details": "جزئیات رویداد",
      "Full view and actions": "نمای کامل و عملیات",
      "Close details": "بستن جزئیات",
      "days": "روز",
      "📅 Gregorian": "📅 میلادی",
      "🗓️ Jalali": "🗓️ شمسی",
      "🌍 Timezone": "🌍 منطقه زمانی",
      "🔔 Status": "🔔 وضعیت",
      "Edit": "ویرایش",
      "Share": "اشتراک",
      "📌 Pin": "📌 سنجاق",
      "Delete": "حذف",
      "Event Note": "یادداشت رویداد",
      "Write a note, checklist, or details…": "یادداشت، فهرست وارسی یا جزئیات…",
      "Reset": "بازنشانی",
      "Save Note": "ذخیره یادداشت",
      "Delete Event?": "رویداد حذف شود؟",
      "This action cannot be undone.": "این کار قابل بازگشت نیست.",

      // Onboarding
      "Never miss what matters": "هیچ چیز مهمی را فراموش نکنید",
      "Add birthdays, appointments, and anything else you want to remember — TimeManager Pro keeps track so you don't have to.": "تولدها، قرارها و هر چیز دیگری را که می‌خواهید به یاد بماند اضافه کنید — تایم‌منیجر پرو به جای شما یادش می‌ماند.",
      "Reminders come straight to Telegram": "یادآوری‌ها مستقیم به تلگرام می‌رسند",
      "No separate app to check. When it's time, you'll get a message right here — once, or on a repeating schedule you choose.": "لازم نیست برنامه دیگری را چک کنید. سر وقتش همین‌جا پیام می‌گیرید، یک‌بار یا با تکراری که خودتان انتخاب می‌کنید.",
      "Gregorian & Jalali, together": "میلادی و شمسی، کنار هم",
      "Every date shows in both calendars automatically. Tap the + button below to add your first event.": "هر تاریخ خودکار در هر دو تقویم نشان داده می‌شود. برای افزودن اولین رویداد دکمه + را بزنید.",
      "Skip": "رد کردن",
      "Next": "بعدی",
      "Get Started": "شروع کنیم",

      // Categories and statuses rendered from JS
      "General": "عمومی",
      "Birthday": "تولد",
      "Work": "کاری",
      "Family": "خانواده",
      "Health": "سلامت",
      "Travel": "سفر",
      "Finance": "مالی",
      "Study": "درسی",
      "Other": "سایر",
      "Pinned": "سنجاق‌شده",
      "Pending": "در انتظار",
      "Processing...": "در حال ارسال…",
      "✅ Sent": "✅ ارسال شد",
      "❌ Failed": "❌ ناموفق",

      // Toasts and errors
      "Please enter an event title.": "لطفاً عنوان رویداد را وارد کنید.",
      "Please select a date.": "لطفاً تاریخ را انتخاب کنید.",
      "Please set the event time, or mark it as an all-day event.": "ساعت رویداد را مشخص کنید یا آن را تمام‌روز علامت بزنید.",
      "Event saved! You'll receive a reminder in Telegram.": "رویداد ذخیره شد! یادآوری‌اش در تلگرام می‌رسد.",
      "Event updated successfully.": "رویداد به‌روزرسانی شد.",
      "Event deleted.": "رویداد حذف شد.",
      "Note saved.": "یادداشت ذخیره شد.",
      "Event pinned to top.": "رویداد بالای فهرست سنجاق شد.",
      "Event unpinned.": "سنجاق رویداد برداشته شد.",
      "Shared!": "به اشتراک گذاشته شد!",
      "Event details copied to clipboard.": "جزئیات رویداد کپی شد.",
      "Could not share. Please try copying manually.": "اشتراک‌گذاری ممکن نشد. دستی کپی کنید.",
      "The note is too long (max 2000 chars).": "یادداشت خیلی بلند است (حداکثر ۲۰۰۰ نویسه).",
      "You have reached the maximum number of events (500).": "به حداکثر تعداد رویداد رسیده‌اید (۵۰۰).",
      "Too many requests. Please slow down.": "درخواست‌ها زیاد است. کمی آهسته‌تر.",
      "Event not found or access denied.": "رویداد پیدا نشد یا دسترسی ندارید.",
      "The request failed. Please try again.": "درخواست ناموفق بود. دوباره تلاش کنید.",
      "Telegram authentication data is incomplete.": "اطلاعات احراز هویت تلگرام ناقص است.",
      "User information was not received from Telegram.": "اطلاعات کاربر از تلگرام دریافت نشد.",
      "Authentication timestamp is invalid.": "زمان احراز هویت معتبر نیست.",
      "Server configuration error. Please contact support.": "خطای پیکربندی سرور. با پشتیبانی تماس بگیرید.",
      "Invalid event ID.": "شناسه رویداد نامعتبر است.",

      // Countdown
      "Today! 🎉": "امروز! 🎉",
      "This event is today!": "این رویداد امروز است!",
      "{days} ago": "{days} پیش",
      "This event was {days} ago": "{days} پیش بوده است",
      "{parts} remaining": "{parts} مانده",
      "{parts} left": "{parts} مانده",
      "day": "روز",
      "week": "هفته",
      "month": "ماه",
      "year": "سال",
      "yr": "سال",
      "mo": "ماه",
    },
  };

  let currentLang = "en";

  function t(text, vars) {
    const table = TRANSLATIONS[currentLang] || {};
    let out = table[text] || text;
    if (vars) {
      Object.keys(vars).forEach((key) => {
        out = out.split(`{${key}}`).join(vars[key]);
      });
    }
    return out;
  }

  // Walks the static markup once and swaps any text node or attribute whose
  // trimmed value has an entry. Unknown strings are left alone, so a missing
  // translation degrades to English rather than to a blank.
  function translateDocument(root) {
    root = root || document.body;

    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        if (!node.nodeValue.trim()) return NodeFilter.FILTER_REJECT;
        const parent = node.parentElement;
        if (!parent || parent.closest("script, style")) return NodeFilter.FILTER_REJECT;
        return NodeFilter.FILTER_ACCEPT;
      },
    });

    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);

    nodes.forEach((node) => {
      const trimmed = node.nodeValue.trim();
      const translated = t(trimmed);
      if (translated !== trimmed) node.nodeValue = node.nodeValue.replace(trimmed, translated);
    });

    ["placeholder", "aria-label", "title"].forEach((attr) => {
      root.querySelectorAll(`[${attr}]`).forEach((el) => {
        const value = (el.getAttribute(attr) || "").trim();
        const translated = t(value);
        if (translated !== value) el.setAttribute(attr, translated);
      });
    });
  }

  function applyLanguage() {
    const code = String(tg?.initDataUnsafe?.user?.language_code || "").toLowerCase();
    currentLang = code.startsWith("fa") ? "fa" : "en";

    const root = document.documentElement;
    root.lang = currentLang;
    root.dir = currentLang === "fa" ? "rtl" : "ltr";

    if (currentLang !== "en") translateDocument();
  }

  const CATEGORY_PLAIN = {
    general: "General",  birthday: "Birthday",
    work:    "Work",      family:   "Family",
    health:  "Health",   travel:   "Travel",
    finance: "Finance",  study:    "Study",
    other:   "Other",
  };

  const REPEAT_LABELS = {
    none: "One time", daily: "🔁 Daily",
    weekly: "🔁 Weekly", monthly: "🔁 Monthly", yearly: "🎂 Yearly",
  };

  const STATUS_LABELS = {
    pending: "Pending", processing: "Processing...",
    done: "✅ Sent", failed: "❌ Failed",
  };

  /* ── Telegram Theme ─────────────────────────────────── */
  // Telegram themeParams key -> CSS custom property read by style.css.
  // Every one of these has a fallback in the stylesheet, so a client that
  // sends only half of them still renders correctly.
  const TG_THEME_MAP = {
    bg_color:                "--tg-bg",
    secondary_bg_color:      "--tg-bg-2",
    section_bg_color:        "--tg-surface",
    text_color:              "--tg-text",
    subtitle_text_color:     "--tg-text-2",
    hint_color:              "--tg-text-muted",
    section_separator_color: "--tg-border",
    link_color:              "--tg-link",
    destructive_text_color:  "--tg-danger",
  };

  function initTelegram() {
    try {
      applyTelegramTheme();
      if (typeof tg.setHeaderColor === "function") tg.setHeaderColor("secondary_bg_color");
      tg.onEvent?.("themeChanged", applyTelegramTheme);
    } catch (_) {}
  }

  function applyTelegramTheme() {
    const root = document.documentElement;
    const params = tg?.themeParams || {};

    Object.entries(TG_THEME_MAP).forEach(([key, cssVar]) => {
      const value = params[key];
      if (typeof value === "string" && value.trim()) {
        root.style.setProperty(cssVar, value.trim());
      } else {
        // Client didn't send this one — drop back to the stylesheet default
        // instead of keeping a stale value from the previous theme.
        root.style.removeProperty(cssVar);
      }
    });

    // Inside the Telegram WebView tg.colorScheme is authoritative:
    // prefers-color-scheme reports the OS setting, which can disagree with
    // the theme the user actually chose in Telegram.
    root.setAttribute("data-tg-scheme", tg?.colorScheme === "dark" ? "dark" : "light");
  }

  /* ── Loading / Status ───────────────────────────────── */
  function setLoading(on) {
    state.isLoading = on;
    document.body.classList.toggle("is-loading", on);
    if (els.syncStatus) els.syncStatus.textContent = on ? "Syncing…" : "Ready";
  }

  function setSkeleton(on) {
    if (els.skeletonState) els.skeletonState.hidden = !on;
  }

  /* ── Toast ──────────────────────────────────────────── */
  let _toastTimer = null;
  function showToast(message, type = "info") {
    if (!els.toast) return;
    els.toast.innerHTML = `
      ${type === "success" ? "✅" : type === "error" ? "❌" : "ℹ️"} ${escapeHtml(message)}
    `;
    els.toast.dataset.type = type;
    els.toast.classList.add("is-visible");
    clearTimeout(_toastTimer);
    _toastTimer = setTimeout(() => els.toast.classList.remove("is-visible"), 2800);

    // Native-feeling haptic nudge on meaningful outcomes (skip routine "info" toasts
    // so this stays purposeful rather than buzzing on everything).
    try {
      if (type === "success") tg?.HapticFeedback?.notificationOccurred?.("success");
      else if (type === "error") tg?.HapticFeedback?.notificationOccurred?.("error");
    } catch (_) {}
  }

  /* ── Custom Confirm Dialog ──────────────────────────── */
  function showConfirm({ title, text, okLabel = "Confirm", icon = "🗑️" }) {
    return new Promise((resolve) => {
      if (!els.confirmOverlay) { resolve(true); return; }

      if (els.confirmTitle) els.confirmTitle.textContent = title;
      if (els.confirmText)  els.confirmText.textContent  = text;
      if (els.confirmOkBtn) els.confirmOkBtn.textContent = okLabel;
      const iconEl = els.confirmOverlay.querySelector(".confirm-icon");
      if (iconEl) iconEl.textContent = icon;

      els.confirmOverlay.hidden = false;
      els.confirmOverlay.removeAttribute("aria-hidden");

      const cleanup = (result) => {
        els.confirmOverlay.hidden = true;
        els.confirmOverlay.setAttribute("aria-hidden", "true");
        resolve(result);
      };

      const handleOk     = () => cleanup(true);
      const handleCancel = () => cleanup(false);
      const handleKey    = (e) => { if (e.key === "Escape") cleanup(false); };

      els.confirmOkBtn?.addEventListener("click", handleOk, { once: true });
      els.confirmCancelBtn?.addEventListener("click", handleCancel, { once: true });
      document.addEventListener("keydown", handleKey, { once: true });
    });
  }

  /* ── API ────────────────────────────────────────────── */
  async function apiPost(path, payload) {
    const response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ initData: state.initData, ...payload }),
    });

    let data = null;
    try { data = await response.json(); } catch (_) {}

    if (!response.ok) {
      const detail = data?.detail || "REQUEST_FAILED";
      throw new Error(detail);
    }
    return data;
  }

  function normalizeError(error) {
    const map = {
      NO_DATA:                "Telegram authentication data is missing.",
      BAD_HASH:               "The Telegram request signature is invalid.",
      EXPIRED:                "Your session has expired. Please reopen the Mini App.",
      INVALID_DATE:           "The date entered is not valid.",
      TITLE_TOO_LONG:         "The title is too long (max 200 chars).",
      NOTE_TOO_LONG:          "The note is too long (max 2000 chars).",
      EVENT_LIMIT_REACHED:    "You have reached the maximum number of events (500).",
      RATE_LIMIT:             "Too many requests. Please slow down.",
      NOT_FOUND_OR_UNAUTHORIZED: "Event not found or access denied.",
      REQUEST_FAILED:         "The request failed. Please try again.",
      NO_HASH:                "Telegram authentication data is incomplete.",
      NO_USER:                "User information was not received from Telegram.",
      INVALID_AUTH_DATE:      "Authentication timestamp is invalid.",
      MISCONFIGURED:          "Server configuration error. Please contact support.",
      INVALID_ID_FORMAT:      "Invalid event ID.",
    };
    const detail = error?.message || "";
    return t(map[detail]) || `Error: ${detail || "Unknown error"}`;
  }

  /* ── Load Events ─────────────────────────────────────── */
  // Long enough that a normal typing burst is one request, short enough that
  // the list still feels like it reacts as you type.
  const SEARCH_DEBOUNCE_MS = 350;

  async function loadEvents(append = false) {
    if (!append) {
      state.skip = 0;
      setSkeleton(true);
      if (els.eventsWrap) els.eventsWrap.innerHTML = "";
      if (els.listState) els.listState.hidden = true;
      if (els.listErrorState) els.listErrorState.hidden = true;
      if (els.noResultsState) els.noResultsState.hidden = true;
      if (els.loadMoreWrap) els.loadMoreWrap.hidden = true;
    }

    setLoading(true);
    try {
      const data = await apiPost("/api/list", {
        skip:   state.skip,
        q:      state.searchTerm.trim(),
        filter: state.currentFilter,
      });
      const newItems = Array.isArray(data.targets) ? data.targets : [];
      state.hasMore = !!data.has_more;

      if (append) {
        state.events = [...state.events, ...newItems];
      } else {
        state.events = newItems;
      }

      state.skip = state.events.length;
      applyFilters();
      renderEvents();
      updateCounters();
      showStatePanel();

      if (els.loadMoreWrap) els.loadMoreWrap.hidden = !state.hasMore;
    } catch (error) {
      state.events = [];
      state.filteredEvents = [];
      if (els.eventsWrap) els.eventsWrap.innerHTML = "";
      if (els.listState) els.listState.hidden = true;
      if (els.listErrorState) els.listErrorState.hidden = false;
      showToast(normalizeError(error), "error");
      if (els.syncStatus) els.syncStatus.textContent = "Error";
    } finally {
      setSkeleton(false);
      setLoading(false);
    }
  }

  function updateCounters() {
    if (els.eventCount) els.eventCount.textContent = String(state.events.length);
  }

  /* ── Filters ────────────────────────────────────────── */
  // The search and the filter are applied by the Mongo query behind /api/list,
  // so what comes back is already the result set. Filtering again here would
  // only ever narrow it to the current page, which is the bug this replaced.
  function applyFilters() {
    state.filteredEvents = state.events;
  }

  /* ── State Panel ────────────────────────────────────── */
  function showStatePanel() {
    const isSearching = state.searchTerm.trim() !== "" || state.currentFilter !== "all";
    const hasResults  = state.filteredEvents.length > 0;

    if (els.listState)      els.listState.hidden      = true;
    if (els.listErrorState) els.listErrorState.hidden = true;
    if (els.noResultsState) els.noResultsState.hidden = true;

    if (hasResults) return;

    // Now that the server does the filtering, an empty page means one of two
    // different things: nothing matched the query, or nothing is saved at all.
    // Comparing events to filteredEvents can no longer tell them apart.
    if (isSearching) {
      if (els.noResultsState) els.noResultsState.hidden = false;
    } else if (els.listState) {
      els.listState.hidden = false;
    }
  }

  /* ── Countdown Logic (English only) ─────────────────── */
  function startOfDay(date) {
    return new Date(date.getFullYear(), date.getMonth(), date.getDate());
  }

  function addMonthsSafe(date, n) {
    const d = new Date(date.getFullYear(), date.getMonth(), 1);
    d.setMonth(d.getMonth() + n);
    const lastDay = new Date(d.getFullYear(), d.getMonth() + 1, 0).getDate();
    d.setDate(Math.min(date.getDate(), lastDay));
    return d;
  }

  function diffParts(from, to) {
    let cursor = startOfDay(from);
    const target = startOfDay(to);
    const totalMs = target - cursor;

    if (totalMs < 0) {
      return { past: true, totalDays: Math.ceil(-totalMs / 86400000) };
    }

    const totalDays = Math.ceil(totalMs / 86400000);

    let years = 0, months = 0;
    while (addMonthsSafe(cursor, 12) <= target) { years++;  cursor = addMonthsSafe(cursor, 12); }
    while (addMonthsSafe(cursor, 1)  <= target) { months++; cursor = addMonthsSafe(cursor, 1); }

    const remDays = Math.ceil((target - cursor) / 86400000);
    const weeks = Math.floor(remDays / 7);
    const days  = remDays % 7;

    return { past: false, years, months, weeks, days, totalDays };
  }

  function pluralize(n, word) {
    // Persian marks no plural on a counted noun: "3 days" is "۳ روز", not "روزها".
    if (currentLang !== "en") return `${n} ${t(word)}`;
    return `${n} ${word}${n !== 1 ? "s" : ""}`;
  }

  function getCountdownData(dateIso) {
    if (!dateIso) return { tone: "long", shortText: "—", fullText: "—", totalDays: 999 };

    const today = startOfDay(new Date());
    const target = startOfDay(new Date(`${dateIso}T00:00:00`));
    const diff = diffParts(today, target);

    if (diff.past) {
      return {
        tone: "past",
        shortText: t("{days} ago", { days: pluralize(diff.totalDays, "day") }),
        fullText: t("This event was {days} ago", { days: pluralize(diff.totalDays, "day") }),
        totalDays: -diff.totalDays,
      };
    }

    if (diff.totalDays === 0) {
      return {
        tone: "today",
        shortText: t("Today! 🎉"),
        fullText: t("This event is today!"),
        totalDays: 0,
      };
    }

    // Build human-readable parts
    const parts = [];
    if (diff.years)  parts.push(pluralize(diff.years, "year"));
    if (diff.months) parts.push(pluralize(diff.months, "month"));
    if (diff.weeks)  parts.push(pluralize(diff.weeks, "week"));
    if (diff.days)   parts.push(pluralize(diff.days, "day"));

    const shortParts = [];
    if (diff.years)  shortParts.push(pluralize(diff.years, "yr"));
    if (diff.months) shortParts.push(pluralize(diff.months, "mo"));
    const extraDays = diff.weeks * 7 + diff.days;
    if (extraDays)   shortParts.push(pluralize(extraDays, "day"));

    const fullText  = t("{parts} remaining", { parts: parts.join("، ") });
    const shortText = t("{parts} left",       { parts: shortParts.join(" ") });

    let tone = "long";
    if      (diff.totalDays <= 3)   tone = "critical";
    else if (diff.totalDays <= 7)   tone = "critical";
    else if (diff.totalDays <= 30)  tone = "soon";
    else if (diff.totalDays <= 90)  tone = "warm";
    else if (diff.totalDays <= 180) tone = "cool";
    else if (diff.totalDays <= 365) tone = "future";

    return { tone, shortText, fullText, totalDays: diff.totalDays };
  }

  /* ── Render Events ───────────────────────────────────── */
  function renderEvents() {
    if (!els.eventsWrap) return;

    if (!state.filteredEvents.length) {
      els.eventsWrap.innerHTML = "";
      showStatePanel();
      return;
    }

    const frag = document.createDocumentFragment();

    state.filteredEvents.forEach((event) => {
      const cd = getCountdownData(event.next_date_iso || event.date_iso);
      const catClass = `cat-${event.category || "general"}`;
      const catLabel = CATEGORY_LABELS[event.category] || "🌐 General";
      const repeatLabel = REPEAT_LABELS[event.repeat] || "One time";

      const art = document.createElement("article");
      art.className = `event-card ${catClass}`;
      art.tabIndex = 0;
      art.setAttribute("role", "button");
      art.setAttribute("aria-label", `Open details for ${event.title}`);
      art.dataset.id = event.id;

      // Progress bar: how close to event (cap at 365 days)
      const progressPct = cd.totalDays <= 0
        ? 100
        : Math.max(5, Math.min(100, Math.round((1 - cd.totalDays / 365) * 100)));

      art.innerHTML = `
        <div class="event-card-top">
          <div class="event-head">
            <h3 class="event-title">${escapeHtml(event.title)}</h3>
            <div class="event-badges">
              ${event.pinned ? '<span class="badge badge-pin">📌 Pinned</span>' : ""}
              <span class="badge ${getCatBadgeClass(event.category)}">${escapeHtml(catLabel)}</span>
              <span class="urgency-badge urgency-${cd.tone}">${escapeHtml(cd.shortText)}</span>
            </div>
          </div>
          <span class="event-repeat">${escapeHtml(repeatLabel)}</span>
        </div>

        <div class="event-progress-wrap">
          <div class="event-progress-bar">
            <div class="event-progress-fill" style="width:${progressPct}%"></div>
          </div>
          <span class="event-progress-label">${
            cd.totalDays <= 0 ? "Today!" :
            cd.totalDays === 0 ? "Today!" :
            `${cd.totalDays}d`
          }</span>
        </div>

        <div class="event-dates">
          <span>📅 ${escapeHtml(event.next_date_iso || event.date_iso || "—")}${
            event.all_day === false && event.time_hm
              ? ` · ${escapeHtml(event.time_hm)}`
              : ""
          }</span>
          <span class="event-dates-sep">•</span>
          <span>🗓️ ${escapeHtml(event.next_date_jalali || event.date_jalali || "—")}</span>
        </div>

        <div class="event-bottom">
          <span class="status-dot status-${escapeHtml(event.notify_status || "pending")}"></span>
          <span>${escapeHtml(t(STATUS_LABELS[event.notify_status] || "Pending"))}</span>
        </div>
      `;

      art.addEventListener("click",   () => openDetail(event.id));
      art.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openDetail(event.id); }
      });

      frag.appendChild(art);
    });

    els.eventsWrap.innerHTML = "";
    els.eventsWrap.appendChild(frag);
    showStatePanel();
  }

  function getCatBadgeClass(cat) {
    const map = {
      birthday: "badge-cat-birthday", work:    "badge-cat-work",
      family:   "badge-cat-family",   health:  "badge-cat-health",
      travel:   "badge-cat-travel",   finance: "badge-cat-finance",
      study:    "badge-cat-study",
    };
    return map[cat] || "";
  }

  /* ── Sheet Management ────────────────────────────────── */
  function openSheet(name, focusTgt = null) {
    state.lastFocusedElement = document.activeElement;
    if (els.sheetOverlay) els.sheetOverlay.hidden = false;

    [els.composerSheet, els.detailSheet].forEach((sheet) => {
      if (!sheet) return;
      const active = sheet.id === name;
      sheet.hidden = !active;
      sheet.setAttribute("aria-hidden", String(!active));
    });

    state.activeSheet = name;
    if (els.openComposerBtn) {
      els.openComposerBtn.setAttribute("aria-expanded", String(name === "composerSheet"));
    }
    updateTgBackButton();
    setTimeout(() => focusTgt?.focus?.(), 40);
  }

  function closeSheets() {
    [els.composerSheet, els.detailSheet].forEach((sheet) => {
      if (!sheet) return;
      sheet.hidden = true;
      sheet.setAttribute("aria-hidden", "true");
    });
    if (els.sheetOverlay) els.sheetOverlay.hidden = true;
    state.activeSheet = null;
    if (els.openComposerBtn) els.openComposerBtn.setAttribute("aria-expanded", "false");
    updateTgBackButton();
    state.lastFocusedElement?.focus?.();
  }

  function updateTgBackButton() {
    if (!tg?.BackButton) return;
    try {
      tg.BackButton.hide();
      tg.BackButton.offClick(handleTgBack);
      if (state.activeSheet) {
        tg.BackButton.onClick(handleTgBack);
        tg.BackButton.show();
      }
    } catch (_) {}
  }

  function handleTgBack() {
    if (closeDatePicker()) return;
    if (state.activeSheet) closeSheets();
  }

  /* ── Composer ────────────────────────────────────────── */
  // Kept in sync with the <option> values in index.html. Anything outside this
  // list (an event saved by a future build, say) falls back to one hour.
  const REMINDER_OFFSETS = [0, 15, 30, 60, 120, 1440, 10080];
  const DEFAULT_OFFSET_MINUTES = 60;

  // An all-day event has no start time, so "15 minutes before" means nothing:
  // it takes a wall-clock reminder instead. A timed event takes an offset.
  function updateAllDayVisibility() {
    const allDay = els.allDay ? els.allDay.checked : true;
    if (els.eventTimeWrap)      els.eventTimeWrap.hidden      = allDay;
    if (els.reminderTimeWrap)   els.reminderTimeWrap.hidden   = !allDay;
    if (els.reminderOffsetWrap) els.reminderOffsetWrap.hidden = allDay;
  }

  function updateRepeatUntilVisibility() {
    if (!els.repeatUntilWrap) return;
    const isRecurring = !!(els.repeat?.value && els.repeat.value !== "none");
    els.repeatUntilWrap.hidden = !isRecurring;
    if (!isRecurring && els.repeatUntil) els.repeatUntil.value = "";
    // Can't pick an end date before the event's own start date.
    if (els.repeatUntil && els.date?.value) els.repeatUntil.min = els.date.value;
  }

  function resetComposer() {
    els.eventForm?.reset();
    if (els.eventId)        els.eventId.value       = "";
    if (els.dateJalali)     els.dateJalali.value     = "";
    if (els.noteCharCount)  els.noteCharCount.textContent = "0 / 2000";
    state.editingEventId = null;
    if (els.composerTitle)    els.composerTitle.textContent    = t("New Event");
    if (els.composerSubtitle) els.composerSubtitle.textContent = "Set title, date and repeat pattern.";
    updateRepeatUntilVisibility();
    updateAllDayVisibility();
    if (els.saveEventBtn)     els.saveEventBtn.innerHTML       = `
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" aria-hidden="true"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg>
      Save Event`;
  }

  function openCreateComposer() {
    resetComposer();
    openSheet("composerSheet", els.title);
  }

  function openEditComposer(event) {
    state.editingEventId = event.id;
    if (els.eventId)    els.eventId.value    = event.id;
    if (els.title)      els.title.value      = event.title      || "";
    if (els.date)       els.date.value       = event.date_iso   || "";
    if (els.dateJalali) els.dateJalali.value = event.date_jalali|| "";
    if (els.repeat)     els.repeat.value     = event.repeat     || "none";
    if (els.category)   els.category.value   = event.category   || "general";
    const allDay = event.all_day !== false;
    if (els.allDay)    els.allDay.checked = allDay;
    if (els.eventTime) els.eventTime.value = event.time_hm || "09:00";

    const spec = (Array.isArray(event.reminders) && event.reminders[0]) || null;
    if (els.reminderTime) {
      const h = String(spec?.mode === "absolute" ? spec.hour   : (event.reminder_hour   ?? 9)).padStart(2, "0");
      const m = String(spec?.mode === "absolute" ? spec.minute : (event.reminder_minute ?? 0)).padStart(2, "0");
      els.reminderTime.value = `${h}:${m}`;
    }
    if (els.reminderOffset) {
      const offset = spec?.mode === "relative" ? Number(spec.offset_minutes) : DEFAULT_OFFSET_MINUTES;
      els.reminderOffset.value = String(
        REMINDER_OFFSETS.includes(offset) ? offset : DEFAULT_OFFSET_MINUTES
      );
    }
    if (els.repeatUntil)  els.repeatUntil.value  = event.repeat_until || "";
    updateRepeatUntilVisibility();
    updateAllDayVisibility();
    if (els.pin)        els.pin.checked      = !!event.pinned;
    if (els.note)       els.note.value       = event.note       || "";
    if (els.noteCharCount) {
      els.noteCharCount.textContent = `${(event.note || "").length} / 2000`;
    }
    if (els.composerTitle)    els.composerTitle.textContent    = t("Edit Event");
    if (els.composerSubtitle) els.composerSubtitle.textContent = "Update the event details.";
    if (els.saveEventBtn)     els.saveEventBtn.textContent     = "Save Changes";
    openSheet("composerSheet", els.title);
  }

  /* ── Detail Panel ────────────────────────────────────── */
  function getEventById(id) {
    return state.events.find((e) => e.id === id) ?? null;
  }

  function openDetail(eventId) {
    const ev = getEventById(eventId);
    if (!ev) return;

    state.detailEventId = eventId;
    const cd = getCountdownData(ev.next_date_iso || ev.date_iso);

    // Basic fields
    if (els.detailEventTitle)    els.detailEventTitle.textContent    = ev.title || "—";
    if (els.detailCategoryBadge) {
      els.detailCategoryBadge.textContent = CATEGORY_LABELS[ev.category] || "General";
      els.detailCategoryBadge.className = `badge ${getCatBadgeClass(ev.category)}`;
    }
    if (els.detailRepeatBadge)  els.detailRepeatBadge.textContent  = REPEAT_LABELS[ev.repeat]  || "One time";
    if (els.detailPinnedBadge)  els.detailPinnedBadge.hidden        = !ev.pinned;
    if (els.detailDateIso) {
      els.detailDateIso.textContent =
        (ev.date_iso || "—") +
        (ev.all_day === false && ev.time_hm ? `  ·  ${ev.time_hm}` : "");
    }
    if (els.detailDateJalali)   els.detailDateJalali.textContent    = ev.date_jalali || "—";
    if (els.detailTimezone)     els.detailTimezone.textContent      = ev.tz_name     || "UTC";
    if (els.detailStatus)       els.detailStatus.textContent        = t(STATUS_LABELS[ev.notify_status] || "—");
    if (els.detailNote)         els.detailNote.value                = ev.note        || "";

    // Pin button label
    if (els.detailPinBtn) {
      els.detailPinBtn.textContent = ev.pinned ? "📌 Unpin" : "📌 Pin";
    }

    // Countdown ring
    if (els.countdownDays) {
      els.countdownDays.textContent = cd.totalDays <= 0 ? "🎉" : String(Math.abs(cd.totalDays));
    }
    if (els.countdownRing) {
      els.countdownRing.className = `countdown-ring${
        cd.tone === "past"  ? " is-past"  :
        cd.tone === "today" ? " is-today" : ""
      }`;
    }
    if (els.detailCountdownText) {
      els.detailCountdownText.textContent = cd.fullText;
    }

    openSheet("detailSheet", els.detailNote);
  }

  /* ── Form Submit (Add / Edit) ────────────────────────── */
  async function submitEventForm(e) {
    e.preventDefault();

    const allDay = els.allDay ? els.allDay.checked : true;
    const eventTime = (els.eventTime?.value || "").trim();
    const [timeH, timeM] = (els.reminderTime?.value || "09:00").split(":");

    const reminders = allDay
      ? [{ mode: "absolute", hour: Number(timeH ?? 9), minute: Number(timeM ?? 0) }]
      : [{ mode: "relative", offset_minutes: Number(els.reminderOffset?.value ?? DEFAULT_OFFSET_MINUTES) }];

    const payload = {
      title:    els.title?.value.trim()    || "",
      date:     els.date?.value            || "",
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
      // Travels with the event so the worker, which has no initData when it
      // fires a reminder, knows which language to write it in.
      lang: currentLang,
      repeat:   els.repeat?.value          || "none",
      category: els.category?.value        || "general",
      note:     els.note?.value.trim()     || "",
      pinned:   !!els.pin?.checked,
      all_day:  allDay,
      time_hm:  allDay ? null : eventTime,
      reminders,
      // Still sent so an older server build keeps scheduling correctly.
      reminder_hour: Number(timeH ?? 9),
      reminder_minute: Number(timeM ?? 0),
      repeat_until: (els.repeat?.value !== "none" && els.repeatUntil?.value) || null,
    };

    if (!payload.title) {
      showToast(t("Please enter an event title."), "error");
      els.title?.focus();
      return;
    }
    if (!payload.date) {
      showToast(t("Please select a date."), "error");
      els.date?.focus();
      return;
    }
    if (!allDay && !eventTime) {
      showToast(t("Please set the event time, or mark it as an all-day event."), "error");
      els.eventTime?.focus();
      return;
    }

    setLoading(true);
    try {
      if (state.editingEventId) {
        // ✅ FIX: event_id (was: eventid)
        await apiPost("/api/edit", { event_id: state.editingEventId, ...payload });
        showToast(t("Event updated successfully."), "success");
      } else {
        await apiPost("/api/add", payload);
        showToast(t("Event saved! You'll receive a reminder in Telegram."), "success");
      }
      closeSheets();
      resetComposer();
      await loadEvents();
    } catch (error) {
      showToast(normalizeError(error), "error");
    } finally {
      setLoading(false);
    }
  }

  /* ── Delete ──────────────────────────────────────────── */
  async function deleteCurrentEvent() {
    const ev = getEventById(state.detailEventId);
    if (!ev) return;

    // ✅ FIX: custom confirm dialog (window.confirm broken in Telegram WebView)
    const ok = await showConfirm({
      title:   "Delete Event?",
      text:    `"${ev.title}" will be permanently removed.`,
      okLabel: "Delete",
      icon:    "🗑️",
    });
    if (!ok) return;

    setLoading(true);
    try {
      // ✅ FIX: event_id (was: eventid)
      await apiPost("/api/delete", { event_id: ev.id });
      closeSheets();
      showToast(t("Event deleted."), "success");
      await loadEvents();
    } catch (error) {
      showToast(normalizeError(error), "error");
    } finally {
      setLoading(false);
    }
  }

  /* ── Save Note ───────────────────────────────────────── */
  async function saveCurrentNote() {
    const ev = getEventById(state.detailEventId);
    if (!ev) return;

    setLoading(true);
    try {
      // ✅ FIX: event_id (was: eventid)
      const data = await apiPost("/api/note", {
        event_id: ev.id,
        note: els.detailNote?.value.trim() || "",
      });
      const target = getEventById(ev.id);
      if (target) target.note = data.note || "";
      showToast(t("Note saved."), "success");
    } catch (error) {
      showToast(normalizeError(error), "error");
    } finally {
      setLoading(false);
    }
  }

  function resetCurrentNote() {
    const ev = getEventById(state.detailEventId);
    if (!ev || !els.detailNote) return;
    els.detailNote.value = ev.note || "";
  }

  /* ── Pin ─────────────────────────────────────────────── */
  async function toggleCurrentPin() {
    const ev = getEventById(state.detailEventId);
    if (!ev) return;

    const nextPinned = !ev.pinned;
    setLoading(true);
    try {
      // ✅ FIX: event_id (was: eventid)
      const data = await apiPost("/api/pin", { event_id: ev.id, pinned: nextPinned });
      ev.pinned = !!data.pinned;
      if (els.detailPinBtn)   els.detailPinBtn.textContent = ev.pinned ? "📌 Unpin" : "📌 Pin";
      if (els.detailPinnedBadge) els.detailPinnedBadge.hidden = !ev.pinned;
      await loadEvents();
      showToast(ev.pinned ? "Event pinned to top." : "Event unpinned.", "success");
    } catch (error) {
      showToast(normalizeError(error), "error");
    } finally {
      setLoading(false);
    }
  }

  /* ── Share ───────────────────────────────────────────── */
  async function shareCurrentEvent() {
    const ev = getEventById(state.detailEventId);
    if (!ev) return;

    const text = [
      `📅 ${ev.title}`,
      `📆 Gregorian: ${ev.date_iso}`,
      ev.all_day === false && ev.time_hm ? `🕒 Time: ${ev.time_hm}` : "",
      `🗓️ Jalali: ${ev.date_jalali}`,
      `🔄 Repeat: ${REPEAT_LABELS[ev.repeat] || "One time"}`,
      `🏷️ Category: ${t(CATEGORY_PLAIN[ev.category] || "General")}`,
      ev.note ? `📝 Note: ${ev.note}` : "",
    ].filter(Boolean).join("\n");

    try {
      if (navigator.share) {
        await navigator.share({ title: ev.title, text });
        showToast(t("Shared!"), "success");
        return;
      }
      await copyToClipboard(text);
      showToast(t("Event details copied to clipboard."), "success");
    } catch (_) {
      showToast(t("Could not share. Please try copying manually."), "error");
    }
  }

  async function copyToClipboard(text) {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return;
    }
    const el = document.createElement("textarea");
    el.value = text;
    el.style.cssText = "position:absolute;left:-9999px;top:0";
    document.body.appendChild(el);
    el.select();
    document.execCommand("copy");
    document.body.removeChild(el);
  }

  /* ── Jalali / Gregorian Sync ─────────────────────────── */
  function format2(n) { return String(n).padStart(2, "0"); }

  // Julian-day based conversion (the jalaali-js algorithm). The previous pair
  // used a 33-year approximation and, worse, assigned to `gy` inside its own
  // `let` initialiser — a temporal dead zone violation that threw for every
  // year above 979, i.e. every real date. Jalali input silently never reached
  // the Gregorian field. This version was checked against the jdatetime output
  // the server produces, for all 25,567 days from 1990 to 2060: no differences
  // and no round-trip failures.
  function div(a, b) { return ~~(a / b); }
  function mod(a, b) { return a - ~~(a / b) * b; }

  const JALALI_BREAKS = [-61, 9, 38, 199, 426, 686, 756, 818, 1111, 1181, 1210,
                         1635, 2060, 2097, 2192, 2262, 2324, 2394, 2456, 3178];

  function jalCal(jy) {
    const bl = JALALI_BREAKS.length;
    const gy = jy + 621;
    let leapJ = -14;
    let jp = JALALI_BREAKS[0];
    if (jy < jp || jy >= JALALI_BREAKS[bl - 1]) throw new RangeError("year out of range");

    let jump = 0;
    for (let i = 1; i < bl; i += 1) {
      const jm = JALALI_BREAKS[i];
      jump = jm - jp;
      if (jy < jm) break;
      leapJ = leapJ + div(jump, 33) * 8 + div(mod(jump, 33), 4);
      jp = jm;
    }

    let n = jy - jp;
    leapJ = leapJ + div(n, 33) * 8 + div(mod(n, 33) + 3, 4);
    if (mod(jump, 33) === 4 && jump - n === 4) leapJ += 1;

    const leapG = div(gy, 4) - div((div(gy, 100) + 1) * 3, 4) - 150;
    const march = 20 + leapJ - leapG;

    if (jump - n < 6) n = n - jump + div(jump + 4, 33) * 33;
    let leap = mod(mod(n + 1, 33) - 1, 4);
    if (leap === -1) leap = 4;

    return { leap, gy, march };
  }

  function gregorianToJulian(gy, gm, gd) {
    let d = div((gy + div(gm - 8, 6) + 100100) * 1461, 4)
          + div(153 * mod(gm + 9, 12) + 2, 5) + gd - 34840408;
    return d - div(div(gy + 100100 + div(gm - 8, 6), 100) * 3, 4) + 752;
  }

  function julianToGregorian(jdn) {
    let j = 4 * jdn + 139361631;
    j = j + div(div(4 * jdn + 183187720, 146097) * 3, 4) * 4 - 3908;
    const i = div(mod(j, 1461), 4) * 5 + 308;
    const gd = div(mod(i, 153), 5) + 1;
    const gm = mod(div(i, 153), 12) + 1;
    const gy = div(j, 1461) - 100100 + div(8 - gm, 6);
    return { gy, gm, gd };
  }

  function jalaliToGregorian(jy, jm, jd) {
    const r = jalCal(jy);
    return julianToGregorian(
      gregorianToJulian(r.gy, 3, r.march) + (jm - 1) * 31 - div(jm, 7) * (jm - 7) + jd - 1
    );
  }

  function gregorianToJalali(gy, gm, gd) {
    let jy = gy - 621;
    const r = jalCal(jy);
    let k = gregorianToJulian(gy, gm, gd) - gregorianToJulian(r.gy, 3, r.march);

    if (k >= 0) {
      if (k <= 185) return { jy, jm: 1 + div(k, 31), jd: mod(k, 31) + 1 };
      k -= 186;
    } else {
      // Previous Jalali year. The leap flag is the one computed for the year we
      // started from, not for the decremented year.
      jy -= 1;
      k += 179;
      if (r.leap === 1) k += 1;
    }

    return { jy, jm: 7 + div(k, 30), jd: mod(k, 30) + 1 };
  }

  function daysInJalaliMonth(jy, jm) {
    if (jm <= 6) return 31;
    if (jm <= 11) return 30;
    // jalCal returns the number of years since the last leap year, so zero —
    // not one — is what marks a leap year. Esfand has 30 days only then.
    return jalCal(jy).leap === 0 ? 30 : 29;
  }

  function syncJalaliFromGregorian() {
    const val = els.date?.value;
    if (!val) { if (els.dateJalali) els.dateJalali.value = ""; return; }
    const [gy, gm, gd] = val.split("-").map(Number);
    if (!gy || !gm || !gd) return;
    const j = gregorianToJalali(gy, gm, gd);
    if (els.dateJalali) els.dateJalali.value = `${j.jy}/${format2(j.jm)}/${format2(j.jd)}`;
  }

  function syncGregorianFromJalali() {
    const raw = (els.dateJalali?.value || "").trim().replace(/-/g, "/");
    if (!raw) return;
    const parts = raw.split("/");
    if (parts.length !== 3) return;
    const [jy, jm, jd] = parts.map(Number);
    if (!jy || !jm || !jd) return;
    try {
      const g = jalaliToGregorian(jy, jm, jd);
      if (els.date) els.date.value = `${g.gy}-${format2(g.gm)}-${format2(g.gd)}`;
    } catch (_) {
      // Out-of-range year: leave the Gregorian field as it was rather than
      // letting the error escape the change handler.
    }
  }

  /* ── Escape HTML ─────────────────────────────────────── */
  function escapeHtml(v) {
    return String(v)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  /* ── Event Bindings ──────────────────────────────────── */
  function bindEvents() {
    // Header / nav
    els.refreshBtn?.addEventListener("click", () => loadEvents());
    els.retryBtn?.addEventListener("click",   () => loadEvents());
    els.emptyAddBtn?.addEventListener("click", openCreateComposer);
    els.openComposerBtn?.addEventListener("click", openCreateComposer);
    els.closeComposerX?.addEventListener("click", closeSheets);
    els.closeDetailX?.addEventListener("click",   closeSheets);
    els.cancelBtn?.addEventListener("click",      closeSheets);
    els.sheetOverlay?.addEventListener("click",   closeSheets);

    // Form
    els.eventForm?.addEventListener("submit", submitEventForm);
    els.date?.addEventListener("change", syncJalaliFromGregorian);
    els.date?.addEventListener("change", updateRepeatUntilVisibility);
    els.repeat?.addEventListener("change", updateRepeatUntilVisibility);
    els.allDay?.addEventListener("change", updateAllDayVisibility);
    els.dateJalali?.addEventListener("change", syncGregorianFromJalali);
    els.dateJalali?.addEventListener("blur",   syncGregorianFromJalali);

    // Note char counter
    els.note?.addEventListener("input", () => {
      const len = els.note.value.length;
      if (els.noteCharCount) els.noteCharCount.textContent = `${len} / 2000`;
    });

    // Search — debounced, because every keystroke would otherwise be a round
    // trip and would burn through the per-minute budget in a few seconds.
    let searchTimer = null;
    els.searchInput?.addEventListener("input", (e) => {
      const value = e.target.value || "";
      if (value.trim() === state.searchTerm.trim()) return;

      state.searchTerm = value;
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => loadEvents(), SEARCH_DEBOUNCE_MS);
    });

    // Filters
    els.filterButtons.forEach((btn) => {
      btn.addEventListener("click", () => {
        const next = btn.dataset.filter || "all";
        els.filterButtons.forEach((b) => b.classList.toggle("is-active", b === btn));
        if (next === state.currentFilter) return;

        state.currentFilter = next;
        clearTimeout(searchTimer);
        loadEvents();
      });
    });

    // Detail actions
    els.detailEditBtn?.addEventListener("click",        () => openEditComposer(getEventById(state.detailEventId)));
    els.detailDeleteBtn?.addEventListener("click",      deleteCurrentEvent);
    els.detailPinBtn?.addEventListener("click",         toggleCurrentPin);
    els.detailShareBtn?.addEventListener("click",       shareCurrentEvent);
    els.detailNoteSaveBtn?.addEventListener("click",    saveCurrentNote);
    els.detailNoteCancelBtn?.addEventListener("click",  resetCurrentNote);

    // Load more
    els.loadMoreBtn?.addEventListener("click", () => loadEvents(true));

    // Onboarding
    els.onboardingSkipBtn?.addEventListener("click", completeOnboarding);
    els.onboardingNextBtn?.addEventListener("click", advanceOnboarding);

    // Keyboard
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        if (closeDatePicker()) return;
        if (els.confirmOverlay && !els.confirmOverlay.hidden) {
          els.confirmOverlay.hidden = true;
          return;
        }
        if (state.activeSheet) closeSheets();
      }
    });
  }

  /* ── Onboarding (first run only) ────────────────────── */
  const ONBOARDING_KEY = "tmp_onboarding_seen_v1";
  const ONBOARDING_STEPS = [
    {
      icon: "🗓️",
      title: "Never miss what matters",
      text: "Add birthdays, appointments, and anything else you want to remember — TimeManager Pro keeps track so you don't have to.",
    },
    {
      icon: "🔔",
      title: "Reminders come straight to Telegram",
      text: "No separate app to check. When it's time, you'll get a message right here — once, or on a repeating schedule you choose.",
    },
    {
      icon: "🌗",
      title: "Gregorian & Jalali, together",
      text: "Every date shows in both calendars automatically. Tap the + button below to add your first event.",
    },
  ];
  let onboardingStep = 0;

  function showOnboardingIfNeeded() {
    if (!els.onboardingOverlay) return;
    try {
      if (localStorage.getItem(ONBOARDING_KEY)) return;
    } catch (_) {
      return; // storage blocked (e.g. private mode) — don't force this on every load
    }
    onboardingStep = 0;
    renderOnboardingStep();
    els.onboardingOverlay.hidden = false;
    els.onboardingOverlay.setAttribute("aria-hidden", "false");
  }

  function renderOnboardingStep() {
    const step = ONBOARDING_STEPS[onboardingStep];
    if (els.onboardingIcon)  els.onboardingIcon.textContent  = step.icon;
    if (els.onboardingTitle) els.onboardingTitle.textContent = t(step.title);
    if (els.onboardingText)  els.onboardingText.textContent  = t(step.text);
    if (els.onboardingNextBtn) {
      els.onboardingNextBtn.textContent =
        onboardingStep === ONBOARDING_STEPS.length - 1 ? t("Get Started") : t("Next");
    }
    if (els.onboardingDots) {
      [...els.onboardingDots.children].forEach((dot, i) => {
        dot.classList.toggle("is-active", i === onboardingStep);
      });
    }
  }

  function advanceOnboarding() {
    try { tg?.HapticFeedback?.selectionChanged?.(); } catch (_) {}
    if (onboardingStep < ONBOARDING_STEPS.length - 1) {
      onboardingStep += 1;
      renderOnboardingStep();
    } else {
      completeOnboarding();
    }
  }

  function completeOnboarding() {
    if (els.onboardingOverlay) {
      els.onboardingOverlay.hidden = true;
      els.onboardingOverlay.setAttribute("aria-hidden", "true");
    }
    try { localStorage.setItem(ONBOARDING_KEY, "1"); } catch (_) {}
  }


  /* ── Date Picker ─────────────────────────────────────── */
  // A typed field cannot be got right in two calendars at once, so both dates
  // are picked instead. Selection rides on CSS scroll snapping: whichever item
  // settles under the highlight band is the value. No drag maths, and momentum
  // scrolling comes free from the browser.
  const DP_ITEM_H = 40;
  const DP_SETTLE_MS = 90;

  const MONTH_NAMES = {
    gregorian: ["January", "February", "March", "April", "May", "June",
                "July", "August", "September", "October", "November", "December"],
    jalali: ["Farvardin", "Ordibehesht", "Khordad", "Tir", "Mordad", "Shahrivar",
             "Mehr", "Aban", "Azar", "Dey", "Bahman", "Esfand"],
  };

  // The server rejects anything outside 1900–2200, so the Jalali range is
  // derived from that rather than guessed — every pickable Jalali date is
  // guaranteed to convert back inside the accepted window.
  const DP_YEAR_MIN = { gregorian: 1900 };
  const DP_YEAR_MAX = { gregorian: 2200 };
  DP_YEAR_MIN.jalali = gregorianToJalali(1900, 1, 1).jy + 1;
  DP_YEAR_MAX.jalali = gregorianToJalali(2200, 12, 31).jy - 1;

  const dp = {
    calendar: "gregorian",
    y: 0, m: 1, d: 1,
    onPick: null,
    allowClear: false,
    settleTimer: null,
  };

  function daysInGregorianMonth(gy, gm) {
    return new Date(Date.UTC(gy, gm, 0)).getUTCDate();
  }

  function dpDaysInMonth() {
    return dp.calendar === "jalali"
      ? daysInJalaliMonth(dp.y, dp.m)
      : daysInGregorianMonth(dp.y, dp.m);
  }

  function dpToIso() {
    if (dp.calendar === "jalali") {
      const g = jalaliToGregorian(dp.y, dp.m, dp.d);
      return `${g.gy}-${format2(g.gm)}-${format2(g.gd)}`;
    }
    return `${dp.y}-${format2(dp.m)}-${format2(dp.d)}`;
  }

  function dpSetFromIso(iso) {
    const [gy, gm, gd] = String(iso || "").split("-").map(Number);
    const valid = gy && gm && gd && gy >= 1900 && gy <= 2200;
    const base = valid ? { gy, gm, gd } : (() => {
      const now = new Date();
      return { gy: now.getFullYear(), gm: now.getMonth() + 1, gd: now.getDate() };
    })();

    if (dp.calendar === "jalali") {
      const j = gregorianToJalali(base.gy, base.gm, base.gd);
      dp.y = j.jy; dp.m = j.jm; dp.d = j.jd;
    } else {
      dp.y = base.gy; dp.m = base.gm; dp.d = base.gd;
    }
  }

  function dpRange(from, to) {
    const out = [];
    for (let i = from; i <= to; i += 1) out.push(i);
    return out;
  }

  function dpFillWheel(el, values, labels, selected) {
    if (!el) return;
    el.innerHTML = values
      .map((value, i) => `<div class="dp-item" role="option" data-value="${value}"` +
                         ` aria-selected="${value === selected}">${escapeHtml(labels[i])}</div>`)
      .join("");
    const index = Math.max(0, values.indexOf(selected));
    el.scrollTop = index * DP_ITEM_H;
  }

  function dpRender(scope = "all") {
    const yMin = DP_YEAR_MIN[dp.calendar];
    const yMax = DP_YEAR_MAX[dp.calendar];
    dp.y = Math.min(Math.max(dp.y, yMin), yMax);
    dp.m = Math.min(Math.max(dp.m, 1), 12);
    dp.d = Math.min(Math.max(dp.d, 1), dpDaysInMonth());

    // Rebuilding a wheel resets its scrollTop, so the two the user is not
    // touching are left alone while a scroll is settling.
    if (scope === "all") {
      const years = dpRange(yMin, yMax);
      dpFillWheel(els.dpYear, years, years.map(String), dp.y);

      const months = dpRange(1, 12);
      dpFillWheel(els.dpMonth, months,
                  MONTH_NAMES[dp.calendar].map((name) => t(name)), dp.m);
    }
    // The day column is always rebuilt: its length depends on the month and,
    // in Esfand, on whether the year is a leap year.
    const days = dpRange(1, dpDaysInMonth());
    dpFillWheel(els.dpDay, days, days.map(String), dp.d);

    dpRenderPreview();
  }

  function dpRenderPreview() {
    if (!els.dpPreview) return;
    const iso = dpToIso();
    const [gy, gm, gd] = iso.split("-").map(Number);
    const j = gregorianToJalali(gy, gm, gd);
    els.dpPreview.textContent = `${iso}  •  ${j.jy}/${format2(j.jm)}/${format2(j.jd)}`;
  }

  function dpMarkSelected(el, value) {
    el?.querySelectorAll(".dp-item").forEach((item) => {
      item.setAttribute("aria-selected", String(Number(item.dataset.value) === value));
    });
  }

  function dpOnScroll(el, field) {
    clearTimeout(dp.settleTimer);
    dp.settleTimer = setTimeout(() => {
      const index = Math.round(el.scrollTop / DP_ITEM_H);
      const item = el.querySelectorAll(".dp-item")[index];
      if (!item) return;

      const value = Number(item.dataset.value);
      if (value === dp[field]) return;

      dp[field] = value;
      dpMarkSelected(el, value);

      // Month length changes with the month and with the Jalali leap year, so
      // the day column is rebuilt whenever either of the others moves.
      if (field === "d") dpRenderPreview();
      else dpRender("days");
    }, DP_SETTLE_MS);
  }

  function openDatePicker({ value, onPick, allowClear = false, calendar = "gregorian" }) {
    dp.onPick = onPick;
    dp.allowClear = allowClear;
    dp.calendar = calendar;
    dpSetFromIso(value);

    els.dpTabs.forEach((tab) => {
      const active = tab.dataset.calendar === dp.calendar;
      tab.classList.toggle("is-active", active);
      tab.setAttribute("aria-selected", String(active));
    });
    if (els.dpClear) els.dpClear.hidden = !allowClear;

    dpRender();
    state.lastFocusedElement = document.activeElement;
    if (els.dpOverlay) {
      els.dpOverlay.hidden = false;
      els.dpOverlay.setAttribute("aria-hidden", "false");
    }
    setTimeout(() => els.dpConfirm?.focus?.(), 40);
  }

  function closeDatePicker() {
    if (!els.dpOverlay || els.dpOverlay.hidden) return false;
    els.dpOverlay.hidden = true;
    els.dpOverlay.setAttribute("aria-hidden", "true");
    dp.onPick = null;
    state.lastFocusedElement?.focus?.();
    return true;
  }

  function setEventDate(iso) {
    if (els.date) els.date.value = iso;
    if (els.dateJalali) {
      if (!iso) { els.dateJalali.value = ""; return; }
      const [gy, gm, gd] = iso.split("-").map(Number);
      const j = gregorianToJalali(gy, gm, gd);
      els.dateJalali.value = `${j.jy}/${format2(j.jm)}/${format2(j.jd)}`;
    }
  }

  function bindDatePicker() {
    const openForEventDate = (calendar) => () => openDatePicker({
      value: els.date?.value,
      calendar,
      onPick: setEventDate,
    });

    els.date?.addEventListener("click", openForEventDate("gregorian"));
    els.dateJalali?.addEventListener("click", openForEventDate("jalali"));
    els.repeatUntil?.addEventListener("click", () => openDatePicker({
      value: els.repeatUntil.value,
      allowClear: true,
      onPick: (iso) => { els.repeatUntil.value = iso; },
    }));

    [[els.dpYear, "y"], [els.dpMonth, "m"], [els.dpDay, "d"]].forEach(([el, field]) => {
      el?.addEventListener("scroll", () => dpOnScroll(el, field), { passive: true });
      el?.addEventListener("click", (e) => {
        const item = e.target.closest(".dp-item");
        if (item) el.scrollTo({ top: item.offsetTop - el.offsetTop, behavior: "smooth" });
      });
      el?.addEventListener("keydown", (e) => {
        const step = e.key === "ArrowDown" ? 1 : e.key === "ArrowUp" ? -1 : 0;
        if (!step) return;
        e.preventDefault();
        el.scrollBy({ top: step * DP_ITEM_H, behavior: "smooth" });
      });
    });

    els.dpTabs.forEach((tab) => tab.addEventListener("click", () => {
      const next = tab.dataset.calendar;
      if (next === dp.calendar) return;
      const iso = dpToIso();          // convert through the current selection
      dp.calendar = next;
      dpSetFromIso(iso);
      els.dpTabs.forEach((other) => {
        const active = other === tab;
        other.classList.toggle("is-active", active);
        other.setAttribute("aria-selected", String(active));
      });
      dpRender();
    }));

    els.dpToday?.addEventListener("click", () => {
      const now = new Date();
      dpSetFromIso(`${now.getFullYear()}-${format2(now.getMonth() + 1)}-${format2(now.getDate())}`);
      dpRender();
    });

    els.dpConfirm?.addEventListener("click", () => {
      const pick = dp.onPick;
      const iso = dpToIso();
      closeDatePicker();
      pick?.(iso);
      try { tg?.HapticFeedback?.selectionChanged?.(); } catch (_) {}
    });

    els.dpClear?.addEventListener("click", () => {
      const pick = dp.onPick;
      closeDatePicker();
      pick?.("");
    });

    els.dpCancel?.addEventListener("click", closeDatePicker);
    els.dpOverlay?.addEventListener("click", (e) => {
      if (e.target === els.dpOverlay) closeDatePicker();
    });
  }

  /* ── Boot ────────────────────────────────────────────── */
  // Before anything renders: the DOM pass rewrites the static markup, and
  // every later render reads currentLang through t().
  applyLanguage();
  initTelegram();
  bindEvents();
  bindDatePicker();
  loadEvents();
  showOnboardingIfNeeded();
})();
'''

CONTENT_3 = r'''/* ── Typeface ───────────────────────────────────────────
   Vazirmatn covers Latin, Persian, and both sets of digits in one family.
   That matters here because the interface mixes them constantly — "1405/01/31"
   and "14:30" sit inside Persian sentences. With two families the digits and
   the words come from different designs with different baselines, which is
   exactly the mismatch this avoids. It also halves the download and removes
   the Google Fonts round trip. */
@font-face {
  font-family: 'Vazirmatn';
  src: url('/static/fonts/Vazirmatn-Regular.woff2') format('woff2');
  font-weight: 400;
  font-style: normal;
  font-display: swap;
}
@font-face {
  font-family: 'Vazirmatn';
  src: url('/static/fonts/Vazirmatn-Medium.woff2') format('woff2');
  font-weight: 500;
  font-style: normal;
  font-display: swap;
}
@font-face {
  font-family: 'Vazirmatn';
  src: url('/static/fonts/Vazirmatn-SemiBold.woff2') format('woff2');
  font-weight: 600;
  font-style: normal;
  font-display: swap;
}
@font-face {
  font-family: 'Vazirmatn';
  src: url('/static/fonts/Vazirmatn-Bold.woff2') format('woff2');
  font-weight: 700;
  font-style: normal;
  font-display: swap;
}
/* 800 is used in a few headings; Bold covers it rather than shipping a
   fifth file for two rules. */
@font-face {
  font-family: 'Vazirmatn';
  src: url('/static/fonts/Vazirmatn-ExtraBold.woff2') format('woff2');
  font-weight: 800;
  font-style: normal;
  font-display: swap;
}

/* ═══════════════════════════════════════════════════════════
   TimeManager Pro — style.css v2.0
   Vibrant, mobile-first, Telegram Mini App
   ═══════════════════════════════════════════════════════════ */

[hidden],
.hidden {
  display: none !important;
}

/* ── Design Tokens ─────────────────────────────────────── */
:root {
  /* Brand */
  --brand: #5b6cf8;
  --brand-2: #8b5cf6;
  --brand-grad: linear-gradient(135deg, #5b6cf8 0%, #8b5cf6 100%);
  --brand-soft: rgba(91, 108, 248, 0.12);
  --brand-text: #ffffff;

  /* Surfaces — Telegram first, original palette as fallback */
  --bg: var(--tg-bg, #f0f2ff);
  --surface: var(--tg-surface, #ffffff);
  --surface-2: var(--tg-bg-2, #f7f8ff);
  --border: var(--tg-border, rgba(91, 108, 248, 0.13));
  --border-strong: rgba(91, 108, 248, 0.22);

  /* Text — Telegram first, original palette as fallback */
  --text: var(--tg-text, #1a1d3a);
  --text-2: var(--tg-text-2, #4a4e78);
  --text-muted: var(--tg-text-muted, #8890b8);

  /* Semantic */
  --danger: var(--tg-danger, #ef4444);
  --danger-soft: rgba(239, 68, 68, 0.1);
  --success: #10b981;
  --warning: #f59e0b;
  --link: var(--tg-link, #5b6cf8);

  /* Category Colors */
  --cat-general:  #5b6cf8;
  --cat-birthday: #ec4899;
  --cat-work:     #3b82f6;
  --cat-family:   #f97316;
  --cat-health:   #ef4444;
  --cat-travel:   #06b6d4;
  --cat-finance:  #22c55e;
  --cat-study:    #a855f7;
  --cat-other:    #78716c;

  /* Urgency Palette */
  --tone-past:     #dc2626;
  --tone-today:    #be185d;
  --tone-critical: #ea580c;
  --tone-soon:     #d97706;
  --tone-warm:     #ca8a04;
  --tone-cool:     #2563eb;
  --tone-future:   #7c3aed;
  --tone-long:     #16a34a;

  /* Shadows */
  --shadow-sm: 0 2px 12px rgba(91, 108, 248, 0.08);
  --shadow-md: 0 8px 32px rgba(91, 108, 248, 0.14);
  --shadow-lg: 0 16px 56px rgba(91, 108, 248, 0.2);
  --shadow-card: 0 2px 8px rgba(26, 29, 58, 0.06), 0 0 0 1px var(--border);

  /* Radius */
  --r-sm: 12px;
  --r-md: 18px;
  --r-lg: 24px;
  --r-xl: 32px;
  --r-pill: 999px;

  /* Spacing */
  --s1: 4px; --s2: 8px; --s3: 12px; --s4: 16px;
  --s5: 20px; --s6: 24px; --s8: 32px;

  /* Layout */
  --safe-bottom: calc(env(safe-area-inset-bottom, 0px) + 24px);
  --app-max: 860px;
  --font: 'Vazirmatn', system-ui, -apple-system, sans-serif;
}

/* ── Dark mode ──────────────────────────────────────────
   Two triggers, one set of tokens:
     1. prefers-color-scheme  — first paint, before app.js runs
     2. [data-tg-scheme]      — set by app.js from tg.colorScheme and
        authoritative, because it wins on specificity and source order.
   Both keep the var(--tg-*) binding, so a palette supplied by Telegram
   still overrides these fallbacks. */
@media (prefers-color-scheme: dark) {
  :root {
    --bg: var(--tg-bg, #0f1024);
    --surface: var(--tg-surface, #1a1d38);
    --surface-2: var(--tg-bg-2, #1f2340);
    --border: var(--tg-border, rgba(91, 108, 248, 0.18));
    --text: var(--tg-text, #eef0ff);
    --text-2: var(--tg-text-2, #a8b0d8);
    --text-muted: var(--tg-text-muted, #5a6090);
    --shadow-card: 0 2px 8px rgba(0,0,0,0.3), 0 0 0 1px var(--border);
  }
}

:root[data-tg-scheme="dark"] {
  --bg: var(--tg-bg, #0f1024);
  --surface: var(--tg-surface, #1a1d38);
  --surface-2: var(--tg-bg-2, #1f2340);
  --border: var(--tg-border, rgba(91, 108, 248, 0.18));
  --text: var(--tg-text, #eef0ff);
  --text-2: var(--tg-text-2, #a8b0d8);
  --text-muted: var(--tg-text-muted, #5a6090);
  --shadow-card: 0 2px 8px rgba(0,0,0,0.3), 0 0 0 1px var(--border);
}

/* Telegram light while the OS is dark — undo the media query above. */
:root[data-tg-scheme="light"] {
  --bg: var(--tg-bg, #f0f2ff);
  --surface: var(--tg-surface, #ffffff);
  --surface-2: var(--tg-bg-2, #f7f8ff);
  --border: var(--tg-border, rgba(91, 108, 248, 0.13));
  --text: var(--tg-text, #1a1d3a);
  --text-2: var(--tg-text-2, #4a4e78);
  --text-muted: var(--tg-text-muted, #8890b8);
  --shadow-card: 0 2px 8px rgba(26, 29, 58, 0.06), 0 0 0 1px var(--border);
}

/* Keeps native controls (the date and time pickers) in the same scheme. */
html[data-tg-scheme="dark"]  { color-scheme: dark; }
html[data-tg-scheme="light"] { color-scheme: light; }

/* ── Reset ──────────────────────────────────────────── */
*, *::before, *::after { box-sizing: border-box; }

html {
  color-scheme: light dark;
  -webkit-text-size-adjust: 100%;
  text-size-adjust: 100%;
}

body {
  margin: 0;
  min-height: 100svh;
  direction: ltr;
  font-family: var(--font);
  font-size: 15px;
  background: var(--bg);
  color: var(--text);
  line-height: 1.55;
  text-align: start;
  overflow-x: hidden;
}

/* Ambient background gradient */
body::before {
  content: '';
  position: fixed;
  inset: 0;
  background:
    radial-gradient(ellipse 80% 50% at 110% -10%, rgba(91,108,248,0.15) 0%, transparent 60%),
    radial-gradient(ellipse 60% 40% at -10% 110%, rgba(139,92,246,0.1) 0%, transparent 60%);
  pointer-events: none;
  z-index: 0;
}

button, input, select, textarea { font: inherit; color: inherit; }
input, select, textarea { text-align: start; }
button { cursor: pointer; border: none; background: none; }
img, svg { max-width: 100%; display: block; }
a { color: var(--link); }

.sr-only {
  position: absolute; width: 1px; height: 1px; margin: -1px;
  border: 0; padding: 0; white-space: nowrap;
  clip-path: inset(50%); overflow: hidden;
}

/* ── Noscript ────────────────────────────────────────── */
.noscript-box {
  margin: 40px auto; max-width: 480px; padding: 32px;
  text-align: center; background: var(--surface);
  border: 1px solid var(--border); border-radius: var(--r-lg);
  position: relative; z-index: 1;
}
.noscript-icon { font-size: 2.5rem; margin-bottom: 12px; }
.noscript-sub { color: var(--text-muted); font-size: 0.88rem; }

/* ── Header ─────────────────────────────────────────── */
.app-header {
  position: sticky; top: 0; z-index: 20;
  display: flex; align-items: center;
  justify-content: space-between; gap: var(--s4);
  width: min(100% - 24px, var(--app-max));
  margin: 0 auto;
  padding: 16px 0 10px;
  backdrop-filter: blur(20px) saturate(180%);
  -webkit-backdrop-filter: blur(20px) saturate(180%);
}

.brand { display: flex; align-items: center; gap: 12px; }

.brand-mark {
  display: grid; place-items: center;
  width: 44px; height: 44px; border-radius: 14px;
  background: var(--brand-grad);
  color: white;
  box-shadow: 0 4px 16px rgba(91, 108, 248, 0.4);
}

.brand-copy { display: flex; flex-direction: column; gap: 1px; }

.brand-title {
  font-size: 1rem; font-weight: 800;
  background: var(--brand-grad);
  -webkit-background-clip: text; background-clip: text;
  -webkit-text-fill-color: transparent;
  line-height: 1.2;
}

.brand-subtitle { color: var(--text-muted); font-size: 0.78rem; font-weight: 500; }

.icon-btn {
  display: grid; place-items: center;
  width: 42px; height: 42px; border-radius: var(--r-sm);
  background: var(--surface);
  border: 1px solid var(--border);
  color: var(--text-2);
  box-shadow: var(--shadow-sm);
  transition: all 150ms ease;
}
.icon-btn:active { transform: scale(0.93); }

/* ── Main ────────────────────────────────────────────── */
.app-main {
  position: relative; z-index: 1;
  width: min(100% - 24px, var(--app-max));
  margin: 0 auto;
  padding: 4px 0 calc(110px + env(safe-area-inset-bottom, 0px));
  display: flex; flex-direction: column; gap: 16px;
}

/* ── Hero Card ───────────────────────────────────────── */
.hero-card {
  display: flex; align-items: center;
  justify-content: space-between; gap: 16px;
  padding: 22px;
  background: var(--brand-grad);
  border-radius: var(--r-xl);
  box-shadow: 0 8px 32px rgba(91,108,248,0.35);
  color: white;
  position: relative;
  overflow: hidden;
}

.hero-card::before {
  content: '';
  position: absolute; inset: 0;
  background: url("data:image/svg+xml,%3Csvg width='120' height='120' viewBox='0 0 120 120' xmlns='http://www.w3.org/2000/svg'%3E%3Ccircle cx='100' cy='20' r='60' fill='rgba(255,255,255,0.05)'/%3E%3Ccircle cx='20' cy='100' r='40' fill='rgba(255,255,255,0.04)'/%3E%3C/svg%3E") no-repeat right top;
}

.hero-badge {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 4px 12px; border-radius: var(--r-pill);
  background: rgba(255,255,255,0.18);
  font-size: 0.78rem; font-weight: 600;
  margin-bottom: 10px; backdrop-filter: blur(8px);
}

.hero-title {
  margin: 0 0 8px; font-size: clamp(1.1rem, 3vw, 1.5rem);
  font-weight: 800; line-height: 1.25; color: white;
}

.hero-text {
  margin: 0; font-size: 0.875rem; opacity: 0.85; line-height: 1.5;
}

.hero-left { flex: 1; min-width: 0; }

.hero-stats {
  display: flex; flex-direction: column; gap: 10px;
  flex-shrink: 0;
}

.stat-box {
  display: flex; flex-direction: column; align-items: center;
  padding: 12px 16px; border-radius: var(--r-md);
  background: rgba(255,255,255,0.15);
  backdrop-filter: blur(8px);
  min-width: 72px;
  text-align: center;
}

.stat-icon { font-size: 1.1rem; }
.stat-value { font-size: 1rem; font-weight: 800; color: white; line-height: 1.2; }
.stat-label { font-size: 0.72rem; opacity: 0.8; font-weight: 500; }

/* ── Toolbar ─────────────────────────────────────────── */
.toolbar { display: flex; flex-direction: column; gap: 12px; }

.toolbar-row {
  display: flex; gap: 8px;
  overflow-x: auto; padding-bottom: 4px;
  -webkit-overflow-scrolling: touch;
  scrollbar-width: none;
}
.toolbar-row::-webkit-scrollbar { display: none; }

.seg-btn {
  border: 1px solid var(--border);
  background: var(--surface);
  color: var(--text-2);
  padding: 8px 14px;
  border-radius: var(--r-pill);
  white-space: nowrap;
  font-size: 0.84rem; font-weight: 600;
  transition: all 150ms ease;
  flex-shrink: 0;
}

.seg-btn.is-active {
  background: var(--brand-grad);
  color: white;
  border-color: transparent;
  box-shadow: 0 4px 12px rgba(91,108,248,0.3);
}

.search-wrap {
  display: flex; align-items: center; gap: 10px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-md);
  padding: 0 14px;
  min-height: 48px;
  box-shadow: var(--shadow-sm);
  transition: border-color 150ms, box-shadow 150ms;
}
.search-wrap:focus-within {
  border-color: var(--brand);
  box-shadow: 0 0 0 4px rgba(91,108,248,0.12);
}

.search-icon { color: var(--text-muted); flex-shrink: 0; }

.search-input {
  flex: 1; border: 0; outline: none;
  background: transparent;
  color: var(--text);
  font-size: 0.9rem;
}

/* ── Skeleton Loading ────────────────────────────────── */
.skeleton-list { display: flex; flex-direction: column; gap: 12px; }

.skeleton-card {
  padding: 20px; background: var(--surface);
  border-radius: var(--r-lg);
  border: 1px solid var(--border);
  display: flex; flex-direction: column; gap: 10px;
}

.sk-line {
  height: 14px; border-radius: var(--r-pill);
  background: linear-gradient(90deg, var(--border) 25%, rgba(91,108,248,0.06) 50%, var(--border) 75%);
  background-size: 200% 100%;
  animation: shimmer 1.5s infinite;
}

.sk-title { height: 18px; width: 65%; }
.sk-sub { width: 85%; }
.sk-sub.short { width: 45%; }

@keyframes shimmer {
  0% { background-position: 200% 0; }
  100% { background-position: -200% 0; }
}

/* ── Empty / Error States ────────────────────────────── */
.empty-state {
  text-align: center;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-xl);
  padding: 36px 24px;
  box-shadow: var(--shadow-sm);
}

.empty-state.is-error {
  border-color: rgba(239,68,68,0.2);
}

.empty-illustration { display: flex; flex-direction: column; align-items: center; gap: 12px; margin-bottom: 20px; }

.empty-circle {
  width: 80px; height: 80px; border-radius: 50%;
  background: var(--brand-soft);
  display: grid; place-items: center;
  font-size: 2rem;
  animation: float 3s ease-in-out infinite;
}

.empty-circle.is-error-circle { background: var(--danger-soft); }

@keyframes float {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(-6px); }
}

.empty-dots { display: flex; gap: 6px; }
.empty-dots span {
  width: 6px; height: 6px; border-radius: 50%;
  background: var(--brand);
  animation: bounce-dot 1.4s ease-in-out infinite;
  opacity: 0.4;
}
.empty-dots span:nth-child(2) { animation-delay: 0.2s; }
.empty-dots span:nth-child(3) { animation-delay: 0.4s; }

@keyframes bounce-dot {
  0%, 80%, 100% { transform: scale(1); opacity: 0.4; }
  40% { transform: scale(1.3); opacity: 1; }
}

.empty-state-title { margin: 0 0 10px; font-size: 1.2rem; font-weight: 800; }
.empty-state-text { margin: 0 0 20px; color: var(--text-muted); font-size: 0.9rem; line-height: 1.6; }

.btn-cta {
  display: inline-flex; align-items: center; gap: 8px;
  font-size: 1rem; padding: 14px 24px;
  box-shadow: 0 6px 20px rgba(91,108,248,0.35);
  margin-bottom: 16px;
}

.empty-hints {
  display: flex; gap: 8px; flex-wrap: wrap; justify-content: center;
  margin-top: 4px;
}

.hint-chip {
  padding: 6px 12px; border-radius: var(--r-pill);
  background: var(--brand-soft);
  color: var(--brand);
  font-size: 0.8rem; font-weight: 600;
  border: 1px solid rgba(91,108,248,0.18);
}

/* ── Event Cards ─────────────────────────────────────── */
.events-wrap { display: flex; flex-direction: column; gap: 12px; }

.event-card {
  padding: 18px;
  border-radius: var(--r-lg);
  background: var(--surface);
  box-shadow: var(--shadow-card);
  border-inline-start: 5px solid var(--cat-general);
  transition: transform 150ms ease, box-shadow 150ms ease;
  cursor: pointer;
  text-align: start;
  position: relative;
  overflow: hidden;
}

.event-card::before {
  content: '';
  position: absolute; inset: 0;
  opacity: 0.04;
  background: linear-gradient(135deg, var(--card-accent, var(--brand)) 0%, transparent 60%);
}

.event-card:active { transform: scale(0.985); }

/* Category accent colors */
.cat-general  { --card-accent: var(--cat-general);  border-inline-start-color: var(--cat-general); }
.cat-birthday { --card-accent: var(--cat-birthday); border-inline-start-color: var(--cat-birthday); }
.cat-work     { --card-accent: var(--cat-work);     border-inline-start-color: var(--cat-work); }
.cat-family   { --card-accent: var(--cat-family);   border-inline-start-color: var(--cat-family); }
.cat-health   { --card-accent: var(--cat-health);   border-inline-start-color: var(--cat-health); }
.cat-travel   { --card-accent: var(--cat-travel);   border-inline-start-color: var(--cat-travel); }
.cat-finance  { --card-accent: var(--cat-finance);  border-inline-start-color: var(--cat-finance); }
.cat-study    { --card-accent: var(--cat-study);    border-inline-start-color: var(--cat-study); }
.cat-other    { --card-accent: var(--cat-other);    border-inline-start-color: var(--cat-other); }

.event-card-top {
  display: flex; align-items: start;
  justify-content: space-between; gap: 12px;
}

.event-head { display: flex; flex-direction: column; gap: 8px; flex: 1; min-width: 0; }

.event-title {
  margin: 0; font-size: 1rem; font-weight: 700;
  line-height: 1.35; color: var(--text);
}

.event-badges { display: flex; gap: 6px; flex-wrap: wrap; }

.badge, .mini-badge {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 3px 10px; border-radius: var(--r-pill);
  font-size: 0.78rem; font-weight: 600;
  background: var(--brand-soft);
  color: var(--brand);
  border: 1px solid rgba(91,108,248,0.15);
}

.badge-muted {
  background: rgba(136,144,184,0.12);
  color: var(--text-muted);
  border-color: transparent;
}

.badge-pin { background: rgba(236,72,153,0.1); color: #be185d; border-color: rgba(236,72,153,0.2); }

/* Category badge colors */
.badge-cat-birthday { background: rgba(236,72,153,0.1); color: #be185d; border-color: rgba(236,72,153,0.2); }
.badge-cat-work     { background: rgba(59,130,246,0.1); color: #1d4ed8; border-color: rgba(59,130,246,0.2); }
.badge-cat-family   { background: rgba(249,115,22,0.1); color: #c2410c; border-color: rgba(249,115,22,0.2); }
.badge-cat-health   { background: rgba(239,68,68,0.1); color: #b91c1c; border-color: rgba(239,68,68,0.2); }
.badge-cat-travel   { background: rgba(6,182,212,0.1); color: #0e7490; border-color: rgba(6,182,212,0.2); }
.badge-cat-finance  { background: rgba(34,197,94,0.1); color: #15803d; border-color: rgba(34,197,94,0.2); }
.badge-cat-study    { background: rgba(168,85,247,0.1); color: #7e22ce; border-color: rgba(168,85,247,0.2); }

/* Urgency badge */
.urgency-badge {
  display: inline-flex; align-items: center;
  padding: 3px 10px; border-radius: var(--r-pill);
  font-size: 0.77rem; font-weight: 700;
  border: none;
}

.urgency-past     { background: rgba(220,38,38,0.15); color: var(--tone-past); }
.urgency-today    { background: rgba(190,24,93,0.15); color: var(--tone-today); }
.urgency-critical { background: rgba(234,88,12,0.15); color: var(--tone-critical); }
.urgency-soon     { background: rgba(217,119,6,0.15); color: var(--tone-soon); }
.urgency-warm     { background: rgba(202,138,4,0.15); color: var(--tone-warm); }
.urgency-cool     { background: rgba(37,99,235,0.12); color: var(--tone-cool); }
.urgency-future   { background: rgba(124,58,237,0.12); color: var(--tone-future); }
.urgency-long     { background: rgba(22,163,74,0.12); color: var(--tone-long); }

.event-repeat {
  color: var(--text-muted); font-size: 0.8rem;
  white-space: nowrap; flex-shrink: 0;
  font-weight: 500;
}

/* Progress Bar (days remaining) */
.event-progress-wrap {
  margin-top: 12px;
  display: flex; align-items: center; gap: 10px;
}

.event-progress-bar {
  flex: 1; height: 5px; border-radius: var(--r-pill);
  background: var(--border);
  overflow: hidden;
}

.event-progress-fill {
  height: 100%; border-radius: var(--r-pill);
  background: var(--card-accent, var(--brand));
  transition: width 600ms ease;
}

.event-progress-label {
  font-size: 0.8rem; font-weight: 700;
  color: var(--card-accent, var(--brand));
  white-space: nowrap; flex-shrink: 0;
}

.event-dates {
  display: flex; gap: 8px; align-items: center;
  margin-top: 10px;
  color: var(--text-muted); font-size: 0.82rem;
  flex-wrap: wrap;
}

.event-dates-sep { opacity: 0.4; }

.event-bottom {
  display: flex; gap: 8px; align-items: center;
  margin-top: 10px; font-size: 0.82rem;
}

.status-dot {
  width: 8px; height: 8px; border-radius: 50%;
  background: var(--text-muted); flex-shrink: 0;
}
.status-dot.status-pending    { background: var(--warning); }
.status-dot.status-processing { background: var(--brand); animation: pulse-dot 1s infinite; }
.status-dot.status-done       { background: var(--success); }
.status-dot.status-failed     { background: var(--danger); }

@keyframes pulse-dot {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}

/* ── Floating Add Button ─────────────────────────────── */
.floating-add-btn {
  position: fixed;
  inset-inline: 16px;
  bottom: var(--safe-bottom);
  z-index: 35;
  display: inline-flex; align-items: center;
  justify-content: center; gap: 10px;
  min-height: 58px; padding: 0 24px;
  border-radius: var(--r-lg);
  background: var(--brand-grad);
  color: white;
  font-size: 1rem; font-weight: 800;
  box-shadow: 0 8px 32px rgba(91,108,248,0.45);
  transition: transform 150ms ease, box-shadow 150ms ease;
  border: none;
}
.floating-add-btn:active {
  transform: scale(0.97);
  box-shadow: 0 4px 16px rgba(91,108,248,0.3);
}

/* ── Sheet Overlay ───────────────────────────────────── */
.sheet-overlay {
  position: fixed; inset: 0; z-index: 40;
  background: rgba(10, 12, 40, 0.5);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
}

/* ── Sheet ───────────────────────────────────────────── */
.sheet {
  position: fixed; inset-inline: 0; bottom: 0; z-index: 41;
  width: min(100%, 920px);
  margin-inline: auto;
  max-height: min(90svh, 860px);
  overflow-y: auto;
  padding: 10px 20px calc(24px + env(safe-area-inset-bottom, 0px));
  background: var(--surface);
  border-radius: 28px 28px 0 0;
  box-shadow: 0 -8px 40px rgba(91,108,248,0.15);
}

.sheet-handle {
  width: 48px; height: 5px; border-radius: var(--r-pill);
  background: var(--border-strong);
  margin: 0 auto 18px;
}

.sheet-head {
  display: flex; align-items: start;
  justify-content: space-between; gap: 16px;
  margin-bottom: 20px;
}

.sheet-title { margin: 0 0 4px; font-size: 1.2rem; font-weight: 800; }
.sheet-subtitle { margin: 0; color: var(--text-muted); font-size: 0.87rem; }

/* ── Form ────────────────────────────────────────────── */
.sheet-form, .detail-card { display: flex; flex-direction: column; gap: 16px; }

.field-group { display: flex; flex-direction: column; gap: 6px; text-align: start; }

.field-label {
  display: flex; align-items: center; gap: 6px;
  font-size: 0.85rem; font-weight: 700;
  color: var(--text-2);
}

.field-optional { font-weight: 400; color: var(--text-muted); margin-inline-start: 2px; }

.field-input {
  width: 100%; min-height: 50px;
  border: 1.5px solid var(--border);
  border-radius: var(--r-md);
  background: var(--surface-2);
  color: var(--text);
  padding: 0 14px;
  outline: none;
  font-weight: 500;
  transition: border-color 150ms, box-shadow 150ms;
}
.field-input:focus {
  border-color: var(--brand);
  box-shadow: 0 0 0 4px rgba(91,108,248,0.12);
}

.field-select { cursor: pointer; appearance: none; -webkit-appearance: none; background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='%238890b8' stroke-width='2.5' stroke-linecap='round'%3E%3Cpath d='m6 9 6 6 6-6'/%3E%3C/svg%3E"); background-repeat: no-repeat; background-position: right 14px center; padding-inline-end: 36px; }

.field-textarea {
  min-height: 120px;
  padding-block: 14px;
  resize: vertical;
  line-height: 1.6;
}

.char-count {
  font-size: 0.78rem; color: var(--text-muted);
  text-align: end;
}

.grid-2 { display: grid; gap: 12px; }

.check-row {
  display: flex; align-items: center; gap: 12px;
  padding: 14px 16px;
  border: 1.5px solid var(--border);
  border-radius: var(--r-md);
  background: var(--surface-2);
  cursor: pointer;
  transition: border-color 150ms;
}
.check-row:hover { border-color: var(--brand); }
.check-row input[type="checkbox"] { width: 18px; height: 18px; accent-color: var(--brand); cursor: pointer; flex-shrink: 0; }
.check-label { display: flex; align-items: center; gap: 8px; font-weight: 600; }
.check-icon { font-size: 1rem; }

/* ── Buttons ─────────────────────────────────────────── */
.form-actions, .detail-actions {
  display: grid; gap: 10px;
}

.btn-primary, .btn-secondary, .btn-danger {
  min-height: 50px; border-radius: var(--r-md);
  padding: 0 20px; font-weight: 700; font-size: 0.95rem;
  display: inline-flex; align-items: center; justify-content: center; gap: 8px;
  transition: all 150ms ease;
  border: 1.5px solid transparent;
}

.btn-primary {
  background: var(--brand-grad);
  color: white;
  box-shadow: 0 4px 16px rgba(91,108,248,0.3);
}
.btn-primary:active { transform: scale(0.97); box-shadow: none; }

.btn-secondary {
  background: var(--surface-2);
  color: var(--text-2);
  border-color: var(--border);
}
.btn-secondary:active { transform: scale(0.97); }

.btn-danger {
  background: rgba(239,68,68,0.1);
  color: var(--danger);
  border-color: rgba(239,68,68,0.25);
}
.btn-danger:active { transform: scale(0.97); }

/* Action buttons in detail */
.detail-actions {
  grid-template-columns: repeat(2, 1fr);
  gap: 8px;
}

.btn-action {
  min-height: 44px; border-radius: var(--r-md);
  padding: 0 12px; font-weight: 700; font-size: 0.83rem;
  display: inline-flex; align-items: center; justify-content: center; gap: 6px;
  transition: all 150ms ease; border: 1.5px solid var(--border);
}

.btn-action-edit   { background: rgba(91,108,248,0.08); color: var(--brand); border-color: rgba(91,108,248,0.2); }
.btn-action-share  { background: rgba(16,185,129,0.08); color: #059669; border-color: rgba(16,185,129,0.2); }
.btn-action-pin    { background: rgba(236,72,153,0.08); color: #be185d; border-color: rgba(236,72,153,0.2); }
.btn-action-delete { background: rgba(239,68,68,0.08); color: var(--danger); border-color: rgba(239,68,68,0.2); }
.btn-action:active { transform: scale(0.96); }

/* ── Detail Card ─────────────────────────────────────── */
.detail-topline { display: flex; gap: 8px; flex-wrap: wrap; }
.detail-event-title { margin: 8px 0; font-size: 1.3rem; font-weight: 800; line-height: 1.3; }

/* Countdown Ring */
.countdown-ring-wrap {
  display: flex; flex-direction: column; align-items: center; gap: 12px;
  padding: 24px; margin: 4px 0;
  background: var(--brand-soft);
  border-radius: var(--r-xl);
  border: 1.5px solid rgba(91,108,248,0.15);
}

.countdown-ring {
  width: 90px; height: 90px; border-radius: 50%;
  background: var(--brand-grad);
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  box-shadow: 0 8px 24px rgba(91,108,248,0.4);
  color: white;
}

.ring-days { font-size: 1.6rem; font-weight: 800; line-height: 1; }
.ring-label { font-size: 0.7rem; font-weight: 600; opacity: 0.85; }

.countdown-ring-text {
  font-size: 0.92rem; font-weight: 700;
  color: var(--brand); text-align: center;
}

/* Past event ring */
.countdown-ring.is-past { background: linear-gradient(135deg, #dc2626, #b91c1c); }
.countdown-ring.is-today { background: linear-gradient(135deg, #be185d, #9d174d); }

.detail-meta-grid {
  display: grid; grid-template-columns: 1fr 1fr; gap: 10px;
}

.detail-meta-box {
  padding: 12px 14px;
  border: 1.5px solid var(--border);
  border-radius: var(--r-md);
  background: var(--surface-2);
}

.detail-meta-label {
  display: block; color: var(--text-muted);
  font-size: 0.78rem; font-weight: 600; margin-bottom: 4px;
}

/* ── Custom Confirm Dialog ───────────────────────────── */
.confirm-overlay {
  position: fixed; inset: 0; z-index: 60;
  background: rgba(10, 12, 40, 0.6);
  backdrop-filter: blur(8px);
  display: flex; align-items: center; justify-content: center;
  padding: 20px;
}

.confirm-dialog {
  background: var(--surface);
  border-radius: var(--r-xl);
  padding: 32px 24px 24px;
  max-width: 320px; width: 100%;
  text-align: center;
  box-shadow: var(--shadow-lg);
  border: 1px solid var(--border);
}

.confirm-icon { font-size: 2.5rem; margin-bottom: 12px; }
.confirm-title { margin: 0 0 8px; font-size: 1.1rem; font-weight: 800; }
.confirm-text { margin: 0 0 24px; color: var(--text-muted); font-size: 0.88rem; }
.confirm-actions { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }

/* ── Onboarding ──────────────────────────────────────── */
.onboarding-dialog { max-width: 340px; padding-top: 36px; }

.onboarding-dots { display: flex; justify-content: center; gap: 7px; margin-bottom: 24px; }
.onboarding-dots span {
  width: 7px; height: 7px; border-radius: 50%;
  background: var(--border-strong);
  transition: background 200ms, width 200ms;
}
.onboarding-dots span.is-active { width: 20px; border-radius: var(--r-pill); background: var(--brand); }

/* ── Toast ───────────────────────────────────────────── */
.toast {
  position: fixed;
  inset-inline: 16px;
  bottom: calc(var(--safe-bottom) + 80px);
  z-index: 70;
  padding: 14px 18px;
  border-radius: var(--r-md);
  background: var(--text);
  color: white;
  font-weight: 600; font-size: 0.9rem;
  box-shadow: var(--shadow-lg);
  opacity: 0; pointer-events: none;
  transform: translateY(12px);
  transition: opacity 200ms ease, transform 200ms ease;
  display: flex; align-items: center; gap: 10px;
}

.toast.is-visible { opacity: 1; transform: translateY(0); }
.toast[data-type="success"] { background: #0d7a4a; }
.toast[data-type="error"] { background: #b91c1c; }

/* ── Load More ───────────────────────────────────────── */
.load-more-wrap { display: flex; justify-content: center; padding: 8px 0; }

.btn-load-more {
  padding: 12px 28px; border-radius: var(--r-pill);
  font-size: 0.88rem; min-height: 44px;
}

/* ── Loading State ───────────────────────────────────── */
.is-loading .floating-add-btn,
.is-loading .icon-btn,
.is-loading .btn-primary,
.is-loading .btn-secondary,
.is-loading .btn-danger,
.is-loading .btn-action {
  opacity: 0.65; pointer-events: none;
}

/* ── Responsive ──────────────────────────────────────── */
@media (min-width: 560px) {
  .grid-2 { grid-template-columns: repeat(2, 1fr); }
  .detail-actions { grid-template-columns: repeat(4, 1fr); }
  .form-actions { grid-template-columns: 1fr 1.5fr; }
}

@media (min-width: 720px) {
  .hero-card { padding: 28px 32px; }
  .hero-stats { flex-direction: row; }
  .stat-box { flex-direction: row; gap: 10px; align-items: center; }

  .sheet {
    left: 50%; right: auto; width: min(92%, 680px);
    transform: translateX(-50%);
    bottom: 20px; border-radius: 28px;
  }
}

/* ── Accessibility ───────────────────────────────────── */
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    transition-duration: 0.01ms !important;
  }
}

:focus-visible {
  outline: 2px solid var(--brand);
  outline-offset: 2px;
}

/* ── Right-to-left ──────────────────────────────────────
   The layout is flexbox and grid throughout, so dir="rtl" on <html> mirrors
   almost everything on its own. These are the few places that needed a hand:
   the select chevron is a background image, which has no logical equivalent,
   and a handful of glyphs read better mirrored. */
[dir="rtl"] .field-select {
  background-position: left 14px center;
  padding-inline-end: 14px;
  padding-inline-start: 36px;
}

/* The wheels are a grid, so RTL would otherwise reverse them to Day | Month |
   Year. Year-first reads correctly in both directions here, because each
   column is a number list rather than a sentence. */
[dir="rtl"] .dp-wheels { direction: ltr; }
[dir="rtl"] .dp-item { direction: rtl; }

[dir="rtl"] .back-btn svg,
[dir="rtl"] .chevron,
[dir="rtl"] .arrow-icon {
  transform: scaleX(-1);
}

/* ── Date picker ────────────────────────────────────────
   Three scroll-snapping columns rather than a typed field. The snap does the
   selection: whichever item ends up under the highlight band is the value, so
   there is no drag maths to get wrong and momentum scrolling comes free from
   the browser. Sits at z-index 50, above the sheet (41) and below the confirm
   dialog (60). */
.field-picker { cursor: pointer; caret-color: transparent; }

.dp-overlay {
  position: fixed; inset: 0; z-index: 50;
  display: flex; align-items: flex-end; justify-content: center;
  background: rgba(10, 12, 30, 0.5);
  backdrop-filter: blur(3px);
}

.dp-dialog {
  width: min(100%, 480px);
  background: var(--surface);
  border-radius: 22px 22px 0 0;
  padding: 8px 18px 18px;
  box-shadow: 0 -8px 40px rgba(0, 0, 0, 0.25);
  animation: dp-rise 0.22s ease-out;
}

@keyframes dp-rise { from { transform: translateY(16px); opacity: 0; } }

@media (prefers-reduced-motion: reduce) {
  .dp-dialog { animation: none; }
}

.dp-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 14px; flex-wrap: wrap; }

.dp-tabs { display: inline-flex; background: var(--surface-2); border-radius: 10px; padding: 3px; gap: 3px; }
.dp-tab {
  border: 0; background: transparent; color: var(--text-2);
  font: inherit; font-size: 0.82rem; font-weight: 600;
  padding: 6px 12px; border-radius: 8px; cursor: pointer;
}
.dp-tab.is-active { background: var(--surface); color: var(--text); box-shadow: var(--shadow-card); }

.dp-wheels {
  position: relative;
  display: grid; grid-template-columns: 1.1fr 1.4fr 0.9fr; gap: 6px;
  height: 200px;
}

.dp-wheel {
  height: 200px; overflow-y: auto; overscroll-behavior: contain;
  scroll-snap-type: y mandatory;
  padding-block: 80px;                 /* (200 - 40) / 2, so item 0 can centre */
  scrollbar-width: none;
  text-align: center;
}
.dp-wheel::-webkit-scrollbar { display: none; }
.dp-wheel:focus-visible { outline: 2px solid var(--brand); outline-offset: 2px; border-radius: 10px; }

.dp-item {
  height: 40px; line-height: 40px;
  scroll-snap-align: center;
  font-size: 0.95rem; color: var(--text-muted);
  cursor: pointer; user-select: none;
  transition: color 0.15s ease, font-weight 0.15s ease;
}
.dp-item[aria-selected="true"] { color: var(--text); font-weight: 700; }

.dp-highlight {
  position: absolute; inset-inline: 0; top: 80px; height: 40px;
  border-radius: 10px; background: var(--brand-soft);
  pointer-events: none;
}

.dp-preview {
  margin: 14px 0 0; text-align: center;
  font-size: 0.9rem; font-weight: 600; color: var(--text-2);
  min-height: 1.2em;
}

.dp-actions { display: flex; gap: 8px; margin-top: 16px; }
.dp-actions .btn-secondary { flex: 1; }
.dp-actions .btn-primary { flex: 1.4; }
'''

FILES: list[tuple[str, str, str, str]] = [
    (
        "templates/index.html",
        "6a7a837d6b75f5dcb3b6bf4a9530103b06d3caa8b9bb24262494daf7de38dc22",
        "4e48559bc94117677d4caa448b875e467aad5cd919c154fdd2a656c9563c957b",
        CONTENT_1,
    ),
    (
        "static/app.js",
        "17dcf2780fc2a509b95ae5bca2ec427fcc43fe15b457fe92711a045556f5ac55",
        "19ca622273b82f65abb98dcfd73444023f053da2640506de02d7a03e7ae3f060",
        CONTENT_2,
    ),
    (
        "static/style.css",
        "026ed3f7784d895403fb0db738840ac6880167e8f5dc54b7b23744aafcaa933a",
        "b47492d21ca5ae6e81a608ba9ab15164da2609020e6405f42805e7ebe86fe4c5",
        CONTENT_3,
    ),
]


def apply_files() -> None:
    for rel, expected_sha, new_sha, content in FILES:
        path = ROOT / rel

        if not path.exists():
            log("MISSING", f"{rel} — not found, skipped")
            continue

        digest = sha(path.read_bytes())

        if digest == new_sha:
            log("skipped", f"{rel} (already applied)")
            continue

        if digest != expected_sha:
            log("MANUAL", f"{rel} — not the version this script expects, left untouched")
            continue

        path.write_bytes(content.encode("utf-8"))
        log("updated", rel)


def main() -> int:
    if not (ROOT / "app" / "main.py").exists():
        print("Run this from the repository root: app/main.py was not found.")
        return 1

    report.append("\nApplying batch 7 — the date picker")
    apply_files()

    print("\n".join(report))
    print(
        "\nDone. Next:\n"
        "  python -m ruff check . ; python -m pytest -q      # expect 101 passing\n"
        "  git add -A\n"
        '  git commit -m "Replace typed date fields with a dual-calendar picker"\n'
    )
    if any("MANUAL" in line or "MISSING" in line for line in report):
        print("Something was left alone — search above for MANUAL or MISSING.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
