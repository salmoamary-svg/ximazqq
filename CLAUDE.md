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

**Correcting a value in `data.json` directly is fine — from the assistant, not from a branch.**
Reconciling a specific transaction or figure by hand (see "the first real reconciliation," below)
means writing straight to `main` via a fresh fetch → targeted edit → diff against the original →
SHA-checked write, the same conflict-safe shape `saveData()` itself uses. Diff the *entire* file
before sending, not just the field being changed — a same-content mistake here once wiped several
top-level keys (see the "near-miss" note under "overnight pass").

## 2026-07-28 — salmoamary-svg — the Spend tab never got the bill/living memo

### What changed
"Last 7 days vs previous 7" read 9,640 SAR of "Everyday: food, coffee, groceries" against
maybe 200 SAR of real spending. `renderWeekly()` and `renderDaily()` predate the whole
bill-vs-living classification layer built earlier — they summed `DATA.transactions` straight
by raw category, so the phone bill, the STC transfer, and the hand-labelled electricity debit
all displayed as grocery money, the exact double-count `sumTx` was built to stop, just in a
view nobody had gone back to touch. "Where money goes — this cycle" and the Month tab's
"Spent this cycle"/"Biggest leak" had the identical problem, one level removed: they read
`DATA.spendCats`, a robot-maintained raw per-category running total with no way to reflect a
`txClass` hand-correction made to one transaction after the fact — the total updates through
`update_app.py`'s own parsing, but a label applied later in the app can never reach it.

Both render functions now filter to `classifyTx(row).kind==='living'` before bucketing —
identical logic to `sumTx`, just applied here for the first time. New `livingCatsThisCycle()`
replaces the `DATA.spendCats` read with the same per-category derivation `paidByTx` already
uses for bills, scoped to the current cycle via `cycleTx`. Against the live data: "Last 7 days"
drops from 9,640 to 756; "Where money goes" drops from including phone/stc entirely to just
`everyday: 122, shopping: 1.5`. `BUILD` → `2026-07-28c`.

### Left alone, on purpose
The Month tab's "was X last cycle" trend still reads `DATA.lastCycleCats`, a frozen snapshot
`update_app.py` takes at rollover — checked directly whether re-deriving it from `DATA.transactions`
would work, and it's off by 10 SAR from a clean re-classification, because the 200-transaction
trim can silently drop the older half of last cycle's data by the time this runs. Rebuilding a
historical total from data that might already be gone would be fragile in a way that fails
silently later, worse than a known, static limitation. The next full cycle tracked entirely under
this classification layer will make the comparison accurate on its own; not worth chasing before
then.

14 checks (`spendtab.js`), including the exact screenshot numbers reproduced against the frozen
fixture and reproduced again against the live `data.json`.

## 2026-07-28 — salmoamary-svg — speed and feel pass

### What was actually slow, and what wasn't
Measured before touching anything, since "make it faster" is easy to spend effort on the wrong
thing: `render()` takes **4ms** even stress-tested at the historical 200-transaction cap (Node,
so a real phone is slower, but nowhere near perceptible either way). `classifyTx()` does get
recomputed more than once per render — `cycleTx`, `paidByTx`, and `sumTx` each walk the
transaction list independently — but at real data volumes that's tens of extra calls, not
thousands, and the measurement above already includes it. **Not worth memoizing** — it would add
a cache-invalidation surface (tied to every place `DATA` is reassigned) to save time nobody would
ever feel. Left alone.

The one real, perceptible latency was the network round-trip: `loadData()` only used to fire
*after* PIN entry, every single time the app opens, even though `data.json` is fetched
unauthenticated — the repo is deliberately public, so there is no security reason to gate the
fetch behind the PIN. It now fires the instant the script runs, in parallel with typing the PIN;
`unlock()` just hides the lock screen over a dashboard that's usually already rendered behind it.
Relies on `loadData` being a hoisted function declaration, called before its own text runs —
verified directly (a mocked `fetch` confirms exactly one call, made before the simulated PIN
entry completes, with `unlock()` triggering no second one).

### Design: tap feedback and a few animations
`-webkit-tap-highlight-color` is disabled globally, which means every tap gave zero visual
acknowledgement on iOS — on a page about money, that reads as "did this register?" more than it
should. Commitment/debt/goal rows get a soft `:active` highlight; buttons (`.setbox button`, the
review card's actions, `+ Add…` rows, the Update FAB) get a gentle press-in. Progress bars
(`.bar>i`) now animate their width instead of snapping, so ticking a commitment visibly fills its
bar rather than jumping. A new `flashSaved()` briefly highlights the balance card on every
successful `saveData()` — the one universal "yes, that was written" signal across every kind of
edit the app supports.

Checked and left alone: contrast ratios across the whole palette are 6.6:1–16:1 (computed
directly, WCAG AA needs 4.5:1 for small text) — nothing to fix there. `BUILD` → `2026-07-28b`.

## 2026-07-28 — salmoamary-svg — reverted: ticking no longer touches the balance

### What changed
Explicitly asked for, less than a day after the balance-on-tick feature shipped: "I think the old
way is better... I want it to be as a checklist that I can just tick." The reasoning given —
"it can duplicate" and "very confusing" — is fair. That feature was the direct cause of nearly
every bug fixed today: the electricity/coffee-run misfire, the need for a separate `bankCoverage`
concept once the fix for that broke the balance math, and a `balanceAnchoredAt`/`tickedAt`
override just to stop a real "Set real balance" correction from fighting the tick feature. Four
interacting pieces of derived state, for one number, on a page meant to be glanced at.

Removed entirely: `manualOutflow`, `bankCoverage`, `effectiveBalance`, the `tickedAt` stamp in
`toggleCommit`, the `balanceAnchoredAt` stamp in both `setBalance()` and the `SAADMONEY-DATA`
email override in `update_app.py`, and the "less X you ticked as paid" note plus its drift nudge.
`computeEnvelope()` reads `DATA.balance` directly again, exactly as it did before any of this
existed. The stray `tickedAt`/`balanceAnchoredAt` keys already sitting in live `data.json` are
inert — nothing reads them anymore, and the existing rollover cleanup (`c.pop("tickedAt")`)
quietly clears the commitment-side ones at the next cycle turn. Not worth a special-purpose write
to scrub early.

**Kept, deliberately:** `tickCandidate` and its role in `toggleCommit` — ticking a commitment
still looks for a matching `review` transaction and labels it, which keeps a real bill out of the
Living envelope. That is a classification fix, not a balance one; it never touches money, only
which bucket a transaction counts toward, and the user's complaint was specifically about the
balance side. The anti-inflation guarantee (`review` transactions only, never `living` ones)
stayed untouched throughout today's changes and still holds.

The new intended workflow, in the user's own words: the checkbox is a to-do list ("did I pay
this, and how much"); reconciling that against real bank activity — matching debits, catching
duplicates, correcting the balance — happens by hand, in conversation, the same way tonight's
phone-bill and STC-transfer corrections were done directly. Automating that fully turned out to
be harder to get right than doing it once, carefully, together.

`balance.js` rewritten from 33 checks (testing machinery that no longer exists) to 17: confirms
the removed functions are gone, ticking never moves `DATA.balance` under any circumstance, a
correction made via "Set real balance" is permanent regardless of later ticks, and the
review-settling classification behavior plus its safety guard both survived untouched. `BUILD` →
`2026-07-28a`.

## 2026-07-27 — salmoamary-svg — ticking a commitment now moves the balance

### What changed
Requested twice, and refused the first time on the grounds that `update_app.py` already does
`balance -= amt` for every alerted debit, so subtracting again would double-count. That
objection only covers bills **the bank saw**. Pay the maid in cash and no alert ever fires,
the balance stays too high, and ticking genuinely should correct it.

`manualOutflow()` subtracts only the part of a ticked commitment that the transactions don't
already account for; `effectiveBalance()` is `DATA.balance` minus that. Derived, never stored,
so the robot remains the only writer of `balance`. Feeds the hero card (with a note saying why
it differs from the bank) and `computeEnvelope`'s cash cap. `BUILD` → `2026-07-27f`.

Self-correcting by construction: tick a cash payment and the balance drops; when the delayed
alert lands, the robot drops `balance` *and* the debit becomes attributable, so the adjustment
falls to zero — one subtraction, not two. That sequence is asserted directly (§5 of `balance.js`).

### tickCandidate had to widen, and that has a cost
Electricity (500) and car insurance (310) are both **below `reviewMin()` (≈635)**, so an alerted
payment for either is classified `living` and never attributed — which meant ticking them
subtracted from the balance a second time. `tickCandidate` now also considers `living`
transactions in the `everyday` bucket, not just `review` ones.

The cost: ticking a bill whose amount happens to equal a genuine grocery run pulls that run out
of the envelope, so **the week can go up**. The earlier "a tick can never raise your week"
guarantee is now narrower: money is never invented (a real debit must exist), only the
unidentified bucket is ever touched, and the review card lists it for one-tap undo. Pinned in
§7 of `balance.js` so a future change can't quietly widen it further.

### Harness
Four suites in the session scratchpad, all against the frozen `fixture.json`:
`harness.js` (58, classification + envelope), `split.js` (16), `cancel.js` (8, token-cancel),
`balance.js` (26, balance adjustment + double-count guard).

## 2026-07-27 — salmoamary-svg — researched, and declined, a service worker

Checked whether there's a better-known fix for the iOS home-screen caching problem than the
`BUILD`-compare-and-banner approach already in place, since it was the direct cause of an
hours-long confusion earlier tonight. A recent (2026) write-up on exactly this problem
(single-file no-build PWA vs. iOS home-screen caching) confirms there isn't one: their working
fix needs Vite build hashing plus a Workbox-orchestrated service worker together — meta tags
alone are "a total failure," and HTTP headers "the bookmarked Home Screen app often ignores."
That's a full build pipeline, which is exactly what this project has deliberately never had.
Given that, `checkBuild()` polling (load + `visibilitychange` + 2-minute interval) plus a
cache-busting reload URL is the right-sized mitigation for this codebase, not a shortcut around
a better answer — there isn't one available without adopting a build step this project rejects.

## 2026-07-27 — salmoamary-svg — CI catches a forgotten BUILD bump

### What changed
New `.github/workflows/verify.yml`, triggered only on a push to `main` that touches `index.html`
(so the robot's data-only `auto-update` commits never run it). Diffs `index.html` against the
previous commit; if it changed but the `^const BUILD=` line didn't, the run fails with an
explicit error instead of silently shipping a build the app can never detect on its own.

Purely advisory — no branch protection exists, so a failing run doesn't block anything, it just
turns the commit red on GitHub. Verified directly against this session's real history (three
commits that each correctly bumped `BUILD`, all pass) and a synthetic commit that edits
`index.html` without bumping it (correctly fails).

## 2026-07-27 — salmoamary-svg — goals stop reading 0%

### What changed
All four goal bars always read 0% — `goals[].cur` was never updated by anything. `goalCur(g)`
(`index.html`) now derives progress at render time from ground truth that mostly already exists,
rather than a number someone has to remember to update:
- **Sister E. / Credit card** — `tgt - debts[id].remaining`. `debts[].remaining` is already
  continuously maintained (the payday sweep credits Sister E., `update_app.py` credits the card
  on every settlement), so this is a pure read of data that was already correct; it just wasn't
  connected to the goal bar.
- **Emergency buffer** — no debt backs it, so there was nothing existing to read. New
  `DATA.bufferSaved`, accumulated by `update_app.py` at each cycle rollover if the buffer
  commitment was paid, *before* `paid`/`actual` are cleared for the new cycle. This is the one
  goal whose progress had to become newly persisted state, since the money it tracks doesn't live
  anywhere else once a cycle turns over.
- Everything else (the robot-maintained "under" goal, any custom goal added in the app) is
  untouched — `goalCur` returns the stored `cur` unchanged.

9 checks (`goals.js`) plus the rollover accumulation logic tested directly against
`update_app.py`'s own code (paid/unpaid/no-actual-entered cases). `BUILD` → `2026-07-27h`.

## 2026-07-27/28 — salmoamary-svg — the first real reconciliation, done by hand

The morning after the overnight pass, two corrections — done in conversation, not by any rule in
the code, which is exactly the workflow the user asked to formalize a few hours later (see
"reverted," above). Both applied as direct, SHA-checked `data.json` writes, diff-verified against
a fresh fetch before sending.

**Phone bill.** The bank's three `phone` debits summed to 1,771.40, but the user's real total was
1,570, reimbursed 400 by a friend for a shared line — net cost 1,170. Set `phone.actual=1170` by
hand; `paidByTx`'s bank-derived 1,771.40 no longer drives the display for a manually-corrected
commitment. The 201.40 gap between the bank total and the stated bill was never resolved — noted
to the user, not chased.

**The STC transfer.** A 2,500 transfer to STC Pay, then redistributed 1,000 to groceries and
1,500 to the housemaid, had produced **three** debit alerts in `data.json` for what was one real
movement of money: an `everyday` 2,500 (a duplicate alert of the same transfer), the `stc` 2,500
itself, and an `everyday` 1,500 (STC Pay's own sub-transfer notification). Separately, a `527.36`
`everyday` debit — the one the electricity mismatch had touched overnight — turned out to
genuinely be the electricity bill, confirmed directly by the user. Applied via `txClass`: `527.36`
→ hand-labelled `elec`; the `stc` 2,500 → `split` across `groc` (1,000) and `maid` (1,500), using
the split feature built for exactly this shape of transaction; the other two entries → `bill` with
no `commit`, so they stop counting as spend without crediting anything a second or third time.
Week went from 782 to 1,310 (green) — the electricity correction was real money, not a bug this
time.

This is the one instance so far of the "review it with you" workflow reasoned about directly:
matching debits, catching duplicates, and reconciling a manual figure against what the bank
reported are all things this session concluded are worth doing by hand, carefully, rather than
automating with rules that keep needing another rule to fix the last one's edge case.

## 2026-07-27 — salmoamary-svg — overnight pass: the tick/balance feature had a real bug, and needed an override

### What triggered this
Minutes after the balance-on-tick feature shipped, the user ticked five commitments and the
balance read 9,117.35 against a real bank balance of ~14,400 — and the week card showed a false
green 1,310. Two separate things were going on:

1. **A real bug.** `tickCandidate` had been widened to match 'living'-classified debits under
   `reviewMin()` (electricity, car insurance), so that ticking them wouldn't double-subtract the
   balance. It worked exactly as built — and that was the problem: ticking "Electricity" for 527
   matched a genuine 527.36 coffee/grocery debit purely because the amount lined up, relabelled it
   `{kind:'bill',commit:'elec',via:'tick'}`, and pulled real living spend out of the week. This was
   flagged as a known residual risk when it shipped and pinned with a test — the test just asserted
   the wrong thing (that this WAS acceptable) instead of catching it.

2. **Pre-existing drift, made visible.** The bank-tracked `balance` (19,074.35) was already ~4,674
   off from reality before any of tonight's ticking — normal drift in an email-parsed running
   total, not something this session caused. The new subtract-on-tick feature stacked its own
   (correct) 9,957 adjustment on top of an already-wrong number, so the gap became large enough to
   look broken instead of quietly being a little off.

### The fix
- **`tickCandidate` reverted to 'review'-only.** It no longer touches anything classified 'living',
  so a tick can never again reclassify real spend as a bill by amount coincidence.
- **`bankCoverage()`** (new, read-only) replaces that widened matching for the balance side only:
  it looks for a same-amount debit of *any* kind this cycle to avoid double-subtracting, but never
  writes `txClass` and never touches the living envelope. Splits the concern that was tangled
  before: "has this money left the account" is no longer the same question as "should this money
  count against my week."
- **The override this needed:** `DATA.balanceAnchoredAt`, stamped by `setBalance()` (and by the
  `SAADMONEY-DATA` email override in `update_app.py`) every time the balance is corrected to a real
  figure. `manualOutflow()` now ignores any tick whose `tickedAt` predates the last anchor — a
  correction is a checkpoint saying "this already accounts for everything I've ticked so far,"
  and only a *later* tick keeps adjusting the display from there. Without this, correcting drift
  and the tick feature would fight each other forever. `tickedAt` clears at cycle rollover
  alongside `paid`/`actual` (`update_app.py`).
- Eager `checkBuild()`: also runs on `visibilitychange` and every 2 minutes, not just on load, so
  a future fix reaches an app left open without needing a full background/reopen.
- A drift nudge: the balance note suggests checking the real balance when unconfirmed ticks exceed
  ~10% of salary — the exact situation that caused tonight's confusion.

Tested against the real scenario directly (34-check `balance.js`, extended): ticking loan/
groceries/Sister A/credit-card with no matching debit owes 9,957; anchoring a fresh 14,400 zeroes
it; a tick made *after* the anchor still adjusts the display; a legacy tick with no `tickedAt`
(predates this feature) is settled by any anchor. `BUILD` → `2026-07-27g`.

### Corrected directly on `main`, not from a branch
The bad tick from tonight had already written `txClass["2026-07-27T16:59:08Z"]=
{kind:'bill',commit:'elec',via:'tick'}` into the live `data.json`, wrongly holding the 527.36
debit out of Living. New code stops this from happening again, but a manual label always wins
(by design), so the stale one would keep overriding forever without a data fix. Removed that one
key via a direct, SHA-checked API write to `main` — the same conflict-safe read-modify-write
`saveData()` itself uses, not a branch merge, so it cannot clobber the robot's concurrent writes.
No other field was touched.

**A near-miss on the first attempt, worth being honest about:** that write went out with placeholder
content instead of the real patched file (`7435986`, commit message literally "placeholder"),
wiping `commitments`/`debts`/`goals`/`transactions` to empty arrays for about two minutes.
Caught on the very next read-back and restored exactly (`5442363`), verified field-by-field
against what was there before. No data was actually lost, but it happened, it's visible in this
history, and the lesson stuck: every write after this one was diff-verified against a fresh fetch
*before* sending, not just sha-checked.

### Still open, deliberately not done tonight
- **Balance drift itself** is not fixed, only made survivable. The email-parsing pipeline will
  keep drifting from the real bank balance over time; periodic use of "Set real balance" (now
  override-safe) is the intended remedy, not a code fix.
- **No new `categorize()` keyword rules.** Musaned/housemaid and the grocery transfer still fall
  into `everyday`. Declined again for the same reason as before: alert text is never stored, so
  there is nothing to test a guessed regex against — needs real sample phrasing from the user.
- **No service worker.** Considered, for auto-refreshing the app shell instead of the tap-to-reload
  banner. Rejected: a misconfigured service worker can get permanently stuck serving a stale
  cache — strictly worse than tonight's problem, and not something to risk unattended without a
  real device to verify on. The eager `checkBuild()` polling above is the safer version of the
  same idea.
- **No LLM-based transaction classification.** Would need sending bank alert text to a third party
  (a privacy stance this project has explicitly rejected) and a paid API key. Not a call to make
  unilaterally overnight.

## 2026-07-27 — salmoamary-svg — one payment, two bills

### What changed
A single 2,500 transfer turned out to be the maid (1,500) and the wife's groceries (1,000)
together, and `txClass` held exactly one `commit` per transaction — so attributing it
overstated whichever bill was picked and left the other showing unpaid.

`txClass[iso]` now takes an optional `split: [{commit,amt},…]` alongside the existing
`commit`. `splitByPlan()` divides the payment in proportion to the commitments' plans, with
the rounding remainder on the last part so the pieces always add back to the whole. The
review card's bill prompt accepts several numbers ("3,4"), and renders a split as
`Housemaid 1,500 + Groceries → wife 1,000`. Old single-`commit` entries still work; there is
no migration. `BUILD` → `2026-07-27e`.

The review card also now keeps listing labels set by hand, not just pending and guessed ones.
Before this a hand label vanished the moment it saved, so a wrong pick had no undo path.

### The amount-guess misfired, as predicted
`matchCommit` attached the *standalone* 1,500 debit to Housemaid because the amount matched
the plan exactly — but the maid was actually paid inside the 2,500. Two payments, one bill.
The guess is visible and one tap from undone, which is the whole reason guesses are surfaced
rather than applied silently, but it is a live example of the failure mode: **amount alone
cannot distinguish two debits that both look like the same bill.**

## 2026-07-27 — salmoamary-svg — a dismissed token prompt silently ate the edit

### What changed
Both token gates (`saveData`, `runRobot`) did `if(!t)return` on a cancelled prompt, so a tick
or a review-card label just appeared not to work — no message, no saved state. They now say
so: `saveData` alerts that nothing was written, and the Update button reads `🔑 token needed`
and stays tappable. `BUILD` bumped to `2026-07-27d`.

This surfaces because **iOS gives a home-screen web app its own storage container, separate
from Safari's**. A token entered in one is simply absent in the other, so the prompt reappears
long after it was first answered, on an app that has been working for weeks. Same for the PIN.

### Repo is public, deliberately
`data.json` — salary, balance, debts, transactions — is readable by anyone, unauthenticated.
This was reviewed and **kept**: it is what lets the app display numbers without a token. The
two alternatives both cost more than they are worth here:
- Flipping the repo private **breaks Pages on a Free plan** (private-repo Pages needs Pro), so
  it would take the app offline, not just secure it.
- Moving `data.json` to a private repo works on Free but needs a second repo, a cross-repo PAT
  in Actions for the robot, and the token becomes mandatory just to *read*.
A "secret" gist is not private — anyone with the link can read it — so it buys nothing.
Revisit only if the account moves to Pro.

## 2026-07-27 — salmoamary-svg — the tick as a manual lever, and shipping that actually arrives

### What changed
The classification fix above was live on Pages and correct, but the phone showed the old
numbers for hours. The app splits its delivery: `data.json` is re-fetched every load with a
`_=Date.now()` buster, while the HTML is whatever copy the phone cached — and an iOS
home-screen app holds the shell indefinitely (Pages only sends `max-age=600`). So the data
reads current while the logic is stale, which looks exactly like a broken fix. Worse, the
Update button reported "✅ up to date" after refreshing only the *data*.

- **`BUILD` constant** (`index.html`, by `REPO`/`API`). `checkBuild()` reads the copy of
  `index.html` on `main`, parses its `BUILD`, and shows a tap-to-reload banner when it
  differs. `reloadFresh()` goes to `?v=<ts>` — a plain `location.reload()` can be served
  back out of the cache that caused the problem. **Bump `BUILD` in the same commit as any
  `index.html` change**, or the banner never fires. The parse is anchored `/^const BUILD=/m`
  because an unanchored one matches its own source further down the same file.
  `checkBuild()` is not awaited and never throws — a version check must not delay the
  numbers or break the app when offline.
- The Update button now distinguishes "data fresh" from "app current".
- **Ticking a commitment** (`toggleCommit`) now also settles the debit that paid it:
  `tickCandidate()` looks for a `review` transaction matching the entered amount (±2% or 25)
  and writes `txClass[iso]={kind:'bill',commit:id,via:'tick'}`. Unticking removes only its
  own `via:'tick'` entries, so hand labels from the review card survive.

Candidates are drawn **only from `review`** — debits already held out of the envelope
pending a decision. Labelling one settles it rather than moving money that was already
counted, and if no debit matches, ticking changes nothing but the tick. That is the
anti-inflation guarantee: no transaction, no credit. It is what makes the tick safe, and
it is tested directly (§12 of the harness).

### What's next
- The build banner cannot help the first time: the version already on the phone predates
  `BUILD`, so it has no `checkBuild()`. One manual cache-bust
  (`https://salmoamary-svg.github.io/ximazqq/?v=2`, then re-add to the home screen) is still
  needed. Every update after that self-announces.
- `BUILD` is bumped by hand. Auto-hashing was rejected — the running DOM has been mutated by
  render, so it cannot be compared against source — but a forgotten bump fails silently.
- The tick matches on amount alone. Two commitments with the same plan paid in one cycle
  will attach to whichever debit is nearest; the review card is the correction path.

### What the other dev must know
- App URL is **https://salmoamary-svg.github.io/ximazqq/** (GitHub Pages, `main`, root).
- No env vars, no migrations. `data.json` was **not** committed.
- The verification harness lives in the session scratchpad, not the repo, and runs against a
  **frozen `fixture.json`** — pointing it at the live `data.json` makes it fail whenever the
  robot writes, which is every few minutes and has nothing to do with the code.

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
