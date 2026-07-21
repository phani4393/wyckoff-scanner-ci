# Live scorecard — how `score_alerts.py` works, end to end

This explains the live alert-scoring feature well enough that someone who has
never seen this repo before can understand what it does, why it exists, and
how to read its output. If you only read one section, read
["Why this exists"](#why-this-exists) and
["Reading the digest"](#reading-the-digest).

**Prerequisite context** — read these first if you haven't:
- [`BACKTEST_FINDINGS.md`](BACKTEST_FINDINGS.md) — the historical backtest.
  10 of 11 Wyckoff signal types show a statistically significant **negative**
  edge vs. a naive "trade the swing" baseline. This doc's feature exists
  because of that finding, not despite it.
- [`SCANNER_FLOW.md`](SCANNER_FLOW.md) — how a signal gets detected and
  turned into a Telegram alert in the first place.
- The main [`README.md`](../README.md) "Live scorecard" section — the short
  version of everything below.

---

## Why this exists

`backtest.py` answers "did these signals work over the last ~20 years of
history?" — and the answer, rigorously tested, was no. But that backtest has
a structural blind spot it can't fix from the inside: **the detectors were
built and debugged by looking at that same history.** Even with no intent to
cherry-pick, it's impossible to fully rule out that the pattern definitions
got tuned (consciously or not) to data everyone could already see.

The only real fix for that is a signal that's scored **after** it fires, on
data nobody had when the detector ran — a true blind test. That's what
`alert_log.py` started capturing on **2026-07-21** (every alert either
scanner sends gets logged to `data/alerts_log.csv`, whether you trade it or
not), and `score_alerts.py` is the piece that actually grades those logged
alerts against what really happened, once enough time has passed to know.

This is a live, out-of-sample complement to the backtest — not a replacement
for it, and not (yet) enough data to draw a real conclusion from. It will
take a long time to accumulate a large enough sample to say anything with
statistical confidence. That's expected and fine; the value right now is that
the mechanism exists and is running, not that it has an answer yet.

---

## The full lifecycle of one alert

Walking through a real example already in the repo, rather than a
hypothetical one:

1. **2026-07-21, 21:21:46 UTC** — the watchlist scanner runs and detects an
   upthrust on **OKTA** at resistance 142.35 (bar closed at 141.71). It sends
   a Telegram alert: *"Upthrust at resistance 142.35 (close 141.71) —
   bearish bias, review for a LONG PUT."*

2. **Same moment** — `alert_log.py` appends one row to `data/alerts_log.csv`:
   ```
   logged_at,source,sym,setup,direction,thesis,underlying
   2026-07-21T21:21:46Z,watchlist,OKTA,upthrust,long_put,"Upthrust at resistance 142.35 (close 141.71) -- bearish bias, review for a LONG PUT",141.71001
   ```
   This row is the **only** record `score_alerts.py` has to work with later —
   it doesn't know or care whether you actually traded it.

3. **Every day after that**, `score-alerts.yml` runs `score_alerts.py`. Each
   run, it checks every logged alert against three horizons — 5, 10, and 20
   **trading** days after the alert date — and scores whichever ones have
   just become old enough. For this OKTA alert (dated 2026-07-21, a Tuesday):
   - **5-day horizon** becomes scoreable on **2026-07-28** (skips the
     weekend of Jul 25-26).
   - **10-day and 20-day horizons** become scoreable further out.
   - Every run before 2026-07-28, this alert is simply skipped — not an
     error, just "not old enough yet."

4. **On or after 2026-07-28**, the next run fetches OKTA's daily bars, finds
   the bar dated 2026-07-21 (the entry), looks 5 trading days ahead, and
   computes:
   - The stock's forward return, **sign-flipped for direction** — OKTA was a
     `long_put` (bearish) alert, so if the stock fell 3%, that scores as
     **+3%** (a win for a put), not −3%.
   - A modeled long-put round-trip P&L via the same Black-Scholes model
     `backtest.py` uses (`options_pricing.py`).
   - It appends one row per horizon to `data/alerts_scored.csv` (see
     [schema](#the-alerts_scoredcsv-schema) below) and never touches that
     row again — scoring is a one-time, idempotent action per
     (alert, horizon) pair.

5. **The next time anyone runs `score_alerts.py`** (scheduled or manual),
   the digest printed (and optionally sent to Telegram) includes this OKTA
   result pooled together with every other scored alert at the same horizon,
   compared against a live baseline (next section).

---

## How the "fair comparison" works

Just measuring "did the alerted stock move the right way" isn't enough —
`BACKTEST_FINDINGS.md`'s whole point is that the honest question is "did it
beat what you'd have gotten just trading the **naive confirmed swing**, with
none of the Wyckoff-specific machinery?" The live scorecard asks the exact
same question, live.

- **`swing_baseline_events()`** (imported from `backtest.py`, not
  reimplemented — so this can never quietly drift out of sync with what the
  historical backtest actually measured) finds every naive swing-low /
  swing-high entry on a ticker, with no Wyckoff filtering at all.
- Those baseline entries are restricted to **on or after 2026-07-21**
  (`LIVE_START_DATE` in `score_alerts.py`) — the day live alert logging
  started. This is deliberate: blending the 20-year historical baseline pool
  into a live-period check would mix two different market regimes into a
  comparison that's supposed to be apples-to-apples (same tickers, same
  calendar period as the alerts being scored).
- Right now, the baseline is only computed for **tickers that already have
  at least one scored alert** — not the full 44-name watchlist. That's a
  cost tradeoff (fewer Twelve Data API calls per run while alert volume is
  still low), not a correctness requirement. It means the baseline will take
  longer to build up "independent tickers" than if it covered the whole
  watchlist. **To widen it:** in `score_alerts.py`, pass
  `backtest.load_tickers()` (the full watchlist) into `live_swing_baseline()`
  instead of the alerted-tickers list built in `build_digest()`.
- Once both the signal side and the baseline side have data from **3 or more
  independent tickers**, the exact same cluster-bootstrap significance test
  from `backtest.py` (`stats_utils.bootstrap_edge_ci`) runs on the edge — a
  95% confidence interval and p-value, resampling by ticker (not by
  individual alert instance) for the same reason the historical backtest
  does: same-ticker instances share trend/vol/beta and aren't independent
  draws. Below 3 tickers on either side, the script says so plainly instead
  of printing a CI that isn't statistically meaningful yet.

---

## Reading the digest

Running `python src/score_alerts.py` prints something like this (real output
from this repo, the day alert logging started — before anything had reached
even the shortest horizon):

```
No newly-eligible (alert, horizon) instances this run.

3 alerts logged since 2026-07-21, none have reached the shortest (5-trading-day) scoring horizon yet. Check back soon.
```

Once alerts start clearing horizons, the digest looks like this (illustrative
— exact numbers will differ once there's real data):

```
LIVE ALERT SCORECARD -- 14/45 (alert, horizon) instances scored across 5 tickers since 2026-07-21
(Out-of-sample complement to docs/BACKTEST_FINDINGS.md -- these signals fired
blind, in real time, before today's outcome was known. Small samples are noisy;
don't trust a breakdown until the CI below actually excludes zero.)

5d horizon: n=5  hit_rate=60.0%  avg_stock_ret=+1.20%  avg_option_pnl=-4.30%
  live-swing baseline (same tickers/period): hit_rate=71.0%  avg_ret=+3.10%  -> edge -11.0pp hit / -1.90pp ret
  Not enough independent tickers yet for a bootstrap CI (have 5, need 3+) -- read the numbers above as descriptive only.

By setup: no setup has 5+ scored instances yet.
```

Field by field:

| Line | What it means |
|---|---|
| `N/M (alert, horizon) instances scored` | `M` = every alert logged × 3 horizons; `N` = how many have actually aged far enough to be graded. The gap between them is just "still pending," not missing data. |
| `Xd horizon: n=... hit_rate=... avg_stock_ret=...` | Across every scored alert at that horizon (all setups pooled), direction-adjusted: a win is "the underlying moved the way the alert's call/put needed it to," regardless of which direction that was. |
| `avg_option_pnl` | Same signals, priced as an actual long call/put round-trip (Black-Scholes, realized vol as IV, theta + spread already netted in) — a stricter, more honest number than the raw stock return. |
| `live-swing baseline ... -> edge ...pp` | The naive "just trade the confirmed swing" control, restricted to the same tickers and the live period only. A negative edge here means the Wyckoff-specific detection is, so far, adding nothing over ignoring it and trading the raw swing — same conclusion `BACKTEST_FINDINGS.md` reached historically, now being checked live. |
| `95% CI (cluster boot, N tickers): ...` **or** `Not enough independent tickers yet` | Whether the edge number above is statistically real or just noise. **Take the edge percentage above literally only when this line gives you a CI** — with too few tickers, treat the raw numbers as anecdotal, not evidence. |
| `By setup` | Same breakdown split by signal type (spring, upthrust, SOS, etc.), but only printed for a setup once it has 5+ scored instances — fewer than that isn't worth reading yet. |

If you pass `--telegram`, the exact same digest text is pushed to your phone
via the existing Telegram bot (`wyckoff_notify.py`) — nothing new to set up.

---

## The `alerts_scored.csv` schema

One row per (alert, horizon) pair that has actually been graded:

| Column | Meaning |
|---|---|
| `logged_at` | Same value as in `alerts_log.csv` — the join key back to the original alert. |
| `sym` | Ticker. |
| `setup` | Signal type (`spring`, `upthrust`, `sos`, `sow`, `lps`, `lpsy`, `sc`, `bc`, `abc`, ...). |
| `direction` | `long_call` or `long_put`, copied from the original alert. |
| `horizon_days` | 5, 10, or 20. |
| `entry_date` | The trading date the alert fired on (matched to a bar in `alerts_log.csv`'s `logged_at`). |
| `exit_date` | `entry_date` + `horizon_days` trading days. |
| `stock_return_pct` | Direction-adjusted forward return of the underlying, as a percentage (already sign-flipped for puts — positive always means "this would have helped a long call/put built on this alert"). |
| `hit` | `1` if `stock_return_pct > 0`, else `0`. |
| `options_pnl_pct` | Modeled long-option round-trip P&L over the same window, or blank if the model couldn't price it (e.g. not enough trailing history for realized vol). |

This file is **safe to commit** — like `alerts_log.csv`, it's derived only
from already-public signal metadata (ticker/setup/direction) plus market
data, never your personal P&L (that's `trades.csv`, which stays local and
gitignored).

---

## Running it

```bash
python src/score_alerts.py              # score whatever's newly eligible, print the digest
python src/score_alerts.py --telegram    # same, plus push the digest to Telegram
```

Needs `TWELVEDATA_API_KEY` (env var or local `twelvedata_api_key.txt`, same
as every other script here). Safe to run as often as you like — rows are
never rescored, and if nothing is newly eligible it just says so and prints
the digest from whatever's already scored.

**Automated:** `.github/workflows/score-alerts.yml` runs it daily, timed
about an hour after the watchlist scan (so that day's alerts and bars have
already settled), and commits `data/alerts_scored.csv` back to the repo —
the same "runner's filesystem is discarded, so persist it as a commit"
pattern `watchlist-scan.yml` uses for `alerts_log.csv`.

---

## What this does NOT prove (yet)

- **It does not (yet) validate or invalidate the historical backtest.** The
  sample is tiny and will stay tiny for a long time — options-relevant
  Wyckoff signals don't fire that often even across 44 tickers. Don't read a
  handful of scored alerts as a verdict either way.
- **It doesn't fix survivorship bias.** That gap (quantified in
  `BACKTEST_FINDINGS.md`) is about the *historical* universe only including
  companies still trading today; it's orthogonal to this feature and still
  needs paid, delisted-inclusive data to close.
- **The baseline is ticker-scoped, not watchlist-wide, for now** — see the
  tradeoff explained above. This makes the road to a 3-ticker cluster
  bootstrap slower than it has to be; it's a deliberate cost choice, not a
  limitation of the method itself.
- **Inherits every assumption in `options_pricing.py`** — realized vol as an
  IV proxy, a synthetic exact-ATM strike, a flat 6% round-trip spread. Same
  caveats as the historical backtest's options-P&L numbers: real IV can move
  independently of realized vol (e.g. an earnings crush), and real spreads
  widen exactly when it matters most.

---

## Tests

`tests/test_score_alerts.py` covers, against synthetic bars/alerts (no
network calls):
- Entry-date lookup hits and misses.
- A bullish (`long_call`) alert scores a positive signed return when the
  stock rises.
- A bearish (`long_put`) alert on the **same underlying move** scores a
  negative signed return — the sign-flip is the single easiest thing to get
  backwards here, so it's tested explicitly.
- An alert whose horizon hasn't elapsed yet produces no row (stays pending).
- Running the scorer twice on the same data produces zero duplicate rows
  (idempotency).

Run with `python -m pytest tests/ -v` from the repo root, alongside the
existing detector regression tests.
