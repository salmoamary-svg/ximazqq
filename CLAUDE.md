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

## 2026-07-27 — salmoamary-svg — paying a bill drained the weekly envelope

### What changed
The week card read `-7,452 SAR` on a day with ~529 SAR of actual living spend. `sumTx()`
summed **every** transaction in the window with no filter, so paying a commitment charged
the Living envelope — money the 47/30/23 split has already set aside under Commitments
(13,085) and Future You (8,500). Every bill was counted twice. The week's 8,885 was
1,771 phone + 2,500 STC + 1,500 housemaid + 2,500 transfer + 85 BNPL, and only 529 living.

New classification layer in `index.html`, all derived from the raw transaction list at
render time — nothing written into `data.json`, so history re-rates on the next load and
the current week repaired itself with no migration:

- `CATCLASS` marks `loan/stc/phone/cc/carins/bnpl/family` as bills that never touch the
  envelope. Unknown categories fall through to living — the safe direction here.
- `UNIDENTIFIED = {everyday}` is the **only** category ever second-guessed. It is
  `update_app.py`'s fallback bucket, so a debit lands there precisely because nothing
  identified it, which is also where mislabelled bank transfers end up. Everything else
  matched a positive keyword rule and always counts, however large.
- Above `reviewMin()` (3 days of the Living plan ≈ 635), an `everyday` debit is
  amount-matched to a commitment (`matchCommit`, ±2% or 25). No match → `review`: held out
  of the envelope and surfaced on a new card, never guessed at.
- `paidByTx()` derives "Paid so far this cycle" from the transactions that paid each
  commitment — display-only, so the robot stays the sole writer of the stored `paid` flag.
  Only 1:1 categories attribute (`CAT2COMMIT`); `stc` and `family` span several
  commitments, so they leave the envelope but never auto-tick anything.

`matchCommit` deliberately ignores `c.paid`: paid state is derived *from* classification,
so reading it there would be circular.

Also fixed `update_app.py:186` — auto-attribution did `cm["actual"]=amt`, so the three
phone debits left `actual` at 460 instead of 1,771.40. It accumulates within a cycle now.

Verified against the real `data.json` with a 41-check harness: living spend 528.86,
week 904.33 (amber), one review item, phone 1,771.40 and maid 1,500 attributed. Rollover
in both directions and `healWeekly` idempotency still hold.

### What's next
- A 1,000 SAR unrecognised transfer still matches the 1,000 `groc` commitment. Genuinely
  ambiguous without merchant text, so it is matched as a `guess` and listed on the review
  card with a one-tap undo rather than resolved silently. Watch whether that misfires.
- An unreviewed large debit is **excluded** from the envelope, so the week reads
  optimistically until labelled. Deliberate — the review card makes it visibly incomplete
  rather than silently wrong — but it does mean an ignored card overstates the week.
- `categorize()` in `update_app.py` was left alone on purpose: alert text is never stored
  (by design, for privacy), so there is nothing to test a new keyword rule against.
- Auto-ticked commitments can't be un-ticked from the commitments list, since the tick is
  derived. The fix path is the review card — correct the transaction, not the symptom.

### What the other dev must know
- No env vars, no schema, no migrations. `data.json` was **not** committed.
- `txClass` is a new optional top-level object in `data.json`, keyed by transaction ISO
  timestamp: `{"2026-07-27T17:00:48Z": {"kind":"bill","commit":"maid"}}`. Written only by
  the app, append-only, so it is safe against the robot's concurrent writes. Orphan keys
  left by the 200-transaction trim are harmless. A manual label always wins over the rules.
- Editing a commitment's `amount` now also moves `reviewMin()` and the match tolerance.

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
