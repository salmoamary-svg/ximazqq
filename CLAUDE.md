# Saad Money

A single-file PWA for tracking a monthly salary cycle. No build step, no framework,
no package manager — `index.html` is the whole app and is served straight from `main`.

- `index.html` — the entire app: markup, styles, and one `<script>` block.
- `update_app.py` — cron job (`.github/workflows/update.yml`, daily 03:30 UTC) that reads
  transaction alerts from Gmail and writes `data.json`.
- `data.json` — live state: balance, commitments, debts, goals, transactions, weekly ledger.
  Both the robot and the app write to it directly on `main`; the app reads it via the
  GitHub contents API at `?ref=main`.

There is nothing to build. To verify a change, syntax-check the artifacts:

    python3 -c "import re;h=open('index.html').read();open('/tmp/c.js','w').write(''.join(re.findall(r'<script>(.*?)</script>',h,re.S)))" && node --check /tmp/c.js
    python3 -m py_compile update_app.py
    python3 -c "import json;json.load(open('data.json'))"

**Do not commit `data.json` from a branch.** The robot rewrites it on `main` roughly daily
and the app writes on every edit, so a branch snapshot will clobber newer state at merge.

## 2026-07-27 — salmoamary-svg — weekly envelope froze at zero after payday

### What changed
The weekly-envelope ledger was pinned at `0 SAR/week` and `0 safe/day` for the whole
July 27 – August 27 cycle. Cause: the payday sweep in `advanceWeekly` rates the new cycle
from whatever balance is on file at the instant of rollover. The salary posted *after* the
sweep, so the balance was still 149.34 and `max(0, 149.34 - 200)` gave an envelope of 0.
The rate only ever recomputed at the next payday, so there was no recovery path.

Two fixes in `index.html`:

- `computeEnvelope()` now bases the pot on the **Living slice** of the 47/30/23 split
  (`grp: "living"`, currently 6,347/mo), capped by cash actually available
  (`balance - cushion`). The old formula handed the entire balance to weekly spending,
  double-counting money already committed to bills and debts, and never matched the
  1,300 / 800 green/amber thresholds the week card renders against.
- New `healWeekly()` re-rates the current cycle once the money arrives, so a late salary
  self-corrects. **Upward only** — spending must never shrink the envelope mid-cycle,
  since the weekly rollover already handles overspend via `selfDebt`.
- New `weeklyView()` wraps advance-then-heal; `render()` and `ensureWeekly()` both go
  through it, so the display is correct on load even before anything is persisted.

Verified against real `data.json`: rate 204.74/day, week one 1,433.19, short 3-day stub
week Aug 24–27 at 614.23. Rollover confirmed in both directions (leftover carries,
overspend borrows). Heal is idempotent and does not re-fire. Confirmed live in production
at 08:03 AM — the app persisted the healed values back to `data.json` on its own.

### What's next
- The week card copy says "Resets every Sunday", but after a payday sweep `weekStart` is
  set to the payday date, so weeks run on the payday's weekday until the next cycle. This
  cycle they run Mon–Sun. Cosmetic mismatch between copy and behaviour; not yet fixed.
- `healWeekly` bails out when `weekly.cycleEnd` disagrees with `paydayAfter(now)`. That is
  a deliberate guard against acting on a ledger that has drifted from the calendar, but it
  means a drifted ledger silently stops self-healing. No detection or warning exists.
- `goals[].cur` are all still 0 and nothing updates them; the goal bars always read 0%.

### What the other dev must know
- No env vars were added or changed. The only secrets are `GMAIL_USER` and
  `GMAIL_APP_PASSWORD`, already set as GitHub Actions repo secrets for the cron workflow.
- No database, no migrations, no Vercel, no Supabase. Nothing to provision or run.
- `data.json` was **not** touched by this change. The app repaired its own weekly block on
  first load. Do not hand-edit it while the app or cron may be writing.
- The envelope is now anchored to the Living commitment, so editing that row's `amount`
  changes the weekly allowance for the rest of the cycle — `healWeekly` will pick up an
  increase immediately, but a decrease will not apply until the next payday by design.
