# Accessibility requirements — Batch 20

Standard: WCAG 2.1 AA, plus two project decisions (D3, D4 in CONSTRAINTS.md).
Every requirement names the tests that prove it. All of them were red on
`main` at `cdeddfb`, and every one was shown green against a throwaway fix in a
scratch copy, so none of them is a test that cannot pass.

| ID | Requirement | WCAG | Proven by |
|----|-------------|------|-----------|
| A11Y-01 | Every dialog starts at its title (the confirm at Cancel), keeps Tab inside, closes on Escape, closes alone when two are stacked (Escape or Telegram's back button), and gives focus back to what opened it — or, where that is gone or never existed, to the event concerned (the next one after a delete, the joined one after a join). Nine dialogs: composer, detail page, date picker, confirm, onboarding, day sheet, share, referral and invite. | 2.1.2, 2.4.3 | `tests/browser/test_a11y_dialogs.py` |
| A11Y-02 | Every way of dismissing a confirmation settles it. One confirmation sends exactly one request. Delete and Pin on the detail page work. | 2.1.1, correctness | `test_a_cancelled_delete_is_not_replayed_by_the_next_confirmation`, `test_one_confirmation_sends_exactly_one_delete`, `test_the_detail_page_delete_button_asks_before_deleting`, `test_the_detail_page_pin_button_sends_the_change` |
| A11Y-03 | Every control exposes name, role and state: every field has a label a screen reader reads (hidden, via `aria-label`, decided in Batch 20), filters expose `aria-pressed`, note and checklist are real tabs with panels and arrow keys while the Gregorian/Jalali switch is a pair of toggle buttons, calendar days are named with their full date and today carries `aria-current="date"`, the skip link is visible on focus, and primary controls answer to a 44×44 px touch area (an invisible margin; the visible size does not change). | 1.3.1, 2.4.7, 3.3.2, 4.1.2, 2.5.5 (AAA, D3) | `test_every_form_field_has_a_label_a_screen_reader_can_read`, `test_every_tab_controls_a_tab_panel`, `test_the_note_and_checklist_tabs_work_like_tabs`, `test_the_calendar_switch_says_which_calendar_is_shown`, `test_filter_buttons_expose_which_filter_is_active`, `test_calendar_days_are_named_with_their_date`, `test_the_skip_link_becomes_visible_when_focused`, `test_primary_controls_are_at_least_44_css_pixels` |
| A11Y-04 | The list has structure: Pinned, Today, Tomorrow and Later are real headings; each event card is one button named by its title and described by its own badges, dates and status (option D, decided in Batch 20 after measuring 0 pixels changed); the list is no live region, and a short status says how many events a filter or a search leaves. | 1.3.1, 4.1.2, 4.1.3 | `test_list_sections_are_headings`, `test_event_titles_are_not_flattened_inside_a_button`, `test_an_event_card_is_announced_with_its_countdown_and_date`, `test_the_event_list_is_not_one_big_live_region`, `test_filtering_and_searching_announce_how_many_events_are_shown` |
| A11Y-05 | Text reads at 4.5:1 or better in every theme: muted text and the brand colour (option 1, decided in Batch 20: each colour keeps its hue and moves only as far as 4.6:1 on every background it sits on; the brand is a fill, `--brand`, and an ink, `--brand-ink`), colours from a Telegram theme included (D4), and the accent colours of badges and buttons (step 9). axe finds nothing serious or critical in eight screens, light and dark. The year strip is no trap for the keyboard (Y1). | 1.4.3, 2.1.1, 4.1.2 | `test_the_fallback_palette_meets_wcag_aa_text_contrast`, `test_axe_finds_no_serious_or_critical_violations`, `test_muted_text_stays_readable_under_a_low_contrast_telegram_theme`, `test_the_year_strip_is_not_a_trap_for_the_keyboard` |
| A11Y-06 | A validation error is marked on its field (`aria-invalid`) and connected to a message (`aria-describedby`) shown under the field, which stays until the user changes that field (decided in Batch 20: visible, and only while there is an error). The title, the date and the time of a timed event alike. The message has its own colour, `--error-text`, at 4.5:1 or better in every theme. | 3.3.1, 3.3.3, 1.4.3 | `test_a_missing_title_is_reported_on_the_field_itself`, `test_a_field_error_stays_until_the_field_changes`, `test_the_other_required_fields_report_their_error_the_same_way`, `test_field_errors_have_a_colour_that_meets_wcag_aa` |
| A11Y-07 | The public countdown page gives the countdown as text (in the image alt), and has a `main` landmark and an `h1`. | 1.1.1, 1.3.1 | `test_the_countdown_image_alt_text_carries_the_countdown`, `test_the_countdown_page_has_a_main_landmark_and_a_heading` |
| A11Y-08 | The three date fields (the event date in both calendars, and repeat-until) open the picker from the keyboard — Enter, and Space the way a button takes it — and say that they open a dialog. A keyboard-only user can date and save an event. | 2.1.1, 4.1.2 | `test_the_date_field_opens_the_picker_from_the_keyboard`, `test_a_keyboard_only_user_can_date_and_save_an_event`, `test_the_date_fields_say_they_open_a_dialog` |
| A11Y-09 | No CSP violation in the console, inside Telegram (every browser test checks this at teardown) or outside it. | security, clean console | `test_opening_the_app_outside_telegram_raises_no_csp_violation` |

## Already met: keep it that way

These were verified in Chromium during the review:

- Persian RTL at 320 px has no horizontal scroll (1.4.10).
- `lang` and `dir` follow the Telegram language.
- The composer focuses its title and returns focus when it closes.
- The viewport allows zoom.

## Order of work (D2)

Step 1 is A11Y-02, the confirm lifecycle and the detail Delete/Pin buttons,
**in one commit**. Fixing the buttons alone would hand the replay bug to
keyboard users, who today cannot reach Delete at all. After that the order is
A11Y-01, 08, 03, 04, 06, 05, 07, 09. Each step makes its tests green and
leaves every other test as it was.

A11Y-01 arrives in three slices on `static/modal.js`, one stack for every
dialog: 2a onboarding, the date picker and the day sheet, with the confirm
moving onto the stack; 2b-1 the status region and the share sheet; 2b-2 the
composer and the detail page; 2c referral and invite, whose tests are written
at the start of 2c. The first review listed six dialogs; there are nine.

The share sheet moved ahead of the composer and the detail page because it
opens on top of the detail page: once that page is a modal dialog, anything
behind it is inert. The same reason put the status region first — everything
outside a modal dialog is dropped from the accessibility tree, so the
validation errors shown while the composer is open would never be announced.

## What the reverse proof taught the real fixes

- **Date fields.** Space acts at keyup, the way native buttons do. The other
  two traps seen in Step 0's sketch are gone since step 2a: the picker opens
  through TMModal with no Enter listener of its own that could fire in the
  same dispatch, and it starts at its title, not at Confirm. The keyboard
  tests ran ten times in a row before the A11Y-08 commit.
- **Day sheet.** Escape must work before focus has arrived, so use a
  document-level handler rather than one on the sheet.
- **Tabs to toggles.** Converting tabs to toggles also means removing
  `role="tablist"` and the `aria-selected` writes in JavaScript.
- **More contrast failures.** axe shows more nodes once the first are fixed.
  The Share action text (#059669 on #ecf9f5, 3.48:1) and the Delete action
  text (#ef4444 on #fef0f0, 3.39:1) also fail.

## Found along the way (not fixed yet)

- **A correction to the first review.** It said the heading and the dates
  inside a card's `role="button"` were never read. Measured in Batch 20:
  Chromium keeps them in its accessibility tree. Phone screen readers usually
  read a button as one stop by its name, though, and that name was "Open
  details for ...", which is why the card now carries a description instead.

- **Share card and the CSP.** The preview is loaded from `WEBAPP_BASE_URL`,
  not from the origin serving the page. Today both are the same; if a custom
  domain ever makes them differ, `img-src 'self'` refuses the preview. Use a
  relative URL, or add that origin to `img-src`.
- **Card images have `alt=""`.** In the share sheet and on the join card, the
  title and countdown inside the image never reach a screen reader (A11Y-07).
- **`alert()` after joining an event you already have.** The code notes that
  `window.confirm` fails in the Telegram WebView; `alert` is at the same risk.
  A message in the status region would do.
- **No axe states for the smaller dialogs.** The axe checks cover the list,
  the composer, the detail page and the month. Confirm, onboarding, picker,
  day sheet, share, invite friends and the join card belong in A11Y-05.

## Still manual (not automatable)

- VoiceOver in Telegram iOS and TalkBack in Telegram Android.
- A keyboard-only pass in Telegram Desktop.
- Contrast under a few real Telegram themes.

Record the results in the pull request.
