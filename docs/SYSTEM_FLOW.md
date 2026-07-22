# System flow — the whole thing, end to end

Text version of [`diagrams/system_flow.html`](diagrams/system_flow.html) (the
interactive one — click any stage to expand it; GitHub's file viewer won't
run its JS, so download the repo and open that file in a browser to
interact with it). Three tracks: the daily automated pipeline, the offline
research/validation track, and the local trader tools.

> **Content verified against commit `af1afe7` (2026-07-22).** See "keeping
> this current" below for how this is meant to stay in sync.

## Track 1 — Daily automated pipeline

1. **Trigger** — three scheduled GitHub Actions workflows: `sp500-scan.yml`
   (~20:20 UTC), `watchlist-scan.yml` (~20:35 UTC), `score-alerts.yml`
   (~21:15 UTC), each DST-aware (dual cron: daylight-time Mar-Oct,
   standard-time Nov-Feb). All three also support manual `workflow_dispatch`
   and running locally.

2. **S&P 500 + AI sweep** (`src/wyckoff_scanner.py`) — the lighter pipeline,
   scanning `data/top50_plus_ai.csv`. Fetches SPY first (aborts entirely if
   that fails). Per ticker: rate limiter (7 calls/min), fetch bars (retry
   2x, drop the unsettled last bar), detect (textbook spring/upthrust
   inline, plus a Weis Wave volume-exhaustion context flag — no
   climax/SOS/SOW/LPS/ABC here, that's watchlist-only), then log + notify.

3. **Watchlist deep scan** (`src/wyckoff_watchlist_scanner.py`) — the full
   pipeline, scanning `data/core_watchlist.csv` (44 names). Same fetch-level
   guardrails, plus a 60-bar floor. Runs the complete detector set (spring/
   upthrust, Buying/Selling Climax + Automatic Reaction, SOS/SOW, LPS/LPSY,
   an ABC correction that only fires on its confirmation day), generates an
   annotated chart per signal, logs every alert, and — local runs only —
   auto-drafts a `trade_journal.py` entry.

4. **Alert persistence** (`src/alert_log.py` → `data/alerts_log.csv`) —
   every alert from both scanners gets one row (timestamp, ticker, setup,
   direction, thesis, price). Since a GitHub Actions runner's filesystem is
   discarded when the job ends, both scan workflows end with a step that
   commits the file back to the repo. A union merge driver
   (`.gitattributes`) lets concurrent commits to this append-only file merge
   without manual conflicts.

5. **Alert scoring** (`src/score_alerts.py`, ~1hr after the watchlist scan)
   — the live, out-of-sample complement to the historical backtest. Reuses
   `backtest.py`'s own `forward_return`, `swing_baseline_events`, and
   `options_pricing.option_pnl_pct` directly (never a reimplementation that
   could drift). `score_pending()` finds alerts now 5/10/20 trading days old
   and scores them (idempotent, bar-index based so weekends/holidays are
   free); `live_swing_baseline()` builds a time-matched fair control
   restricted to dates on/after `LIVE_START_DATE` (2026-07-21), so it never
   mixes with the 20-year historical baseline. The digest leads with simple
   per-alert lines, then the fuller scorecard (hit rate, live-baseline edge,
   cluster-bootstrap 95% CI) — Telegram only fires when something's newly
   scored. Persists `data/alerts_scored.csv` back to the repo the same way.

## Track 2 — Research / validation (offline, run manually)

**Historical backtest** (`src/backtest.py`) — reuses the exact detector
functions running live, so it tests the same logic actually in production.
For every historical signal instance: forward return + modeled option P&L
vs. a matched "naive swing" baseline, with a cluster-bootstrap 95% CI/p-value
(resamples by ticker, not by instance). **Verdict: 10 of 11 signal types
have a statistically significant negative edge** (p<0.001); ABC is the sole
exception — inconclusive, not "proven neutral." Full results in
[`BACKTEST_FINDINGS.md`](BACKTEST_FINDINGS.md). Supporting modules:
- `options_pricing.py` — Black-Scholes long-option P&L, realized vol as an IV proxy.
- `stats_utils.py` — the cluster bootstrap.
- `survivorship_sensitivity.py` — the baseline flips negative at a 5% hypothetical company-failure rate.
- `regime_analysis.py` — tested and ruled out SPY's own trend as an explanation for the edge.

## Track 3 — Local trader tools (manual, never run in CI)

Read/write `trades.csv`, which stays on your machine.
- **`trade_journal.py`** — log trades with decision context; promote/discard drafts; earnings-proximity warnings.
- **`trade_analytics.py`** — your own realized win rate, expectancy, profit factor, and behavioral leaks.
- **`pretrade.py`** — realized-vol expected move for strike/target sizing (no real IV — free tier).
- **`correlation.py`** — effective independent bets, SPY correlation, premium at risk; includes a what-if mode.

## Keeping this current

Both this file and `diagrams/system_flow.html` are meant to track the actual
system. Two things help that stay true rather than drift:

1. **The HTML is data-driven.** All stage/step text lives in one `TRACKS`
   array at the top of its `<script>`, rendered by a small `render()`
   function — updating a stage is a small, isolated data edit, not hunting
   through repeated markup.
2. **`tests.yml` runs a lightweight staleness check on every push.** If a
   flow-relevant file (any scanner/scoring/backtest/tool script, or a
   scan/score workflow file) changes without this `.md` or the diagram
   `.html` changing in the same push, it emits a visible `::warning::`
   annotation on the run. It's a nudge, not a hard gate — a bot commit or a
   rushed fix shouldn't be blocked from merging just because this diagram
   wasn't touched in the same push, but the warning is hard to miss on the
   commit's checks page.

Neither guarantees this is perfectly in sync at every moment — a human (or
agent) still has to actually read the warning and update the content. But
between the two, staying current is a five-minute edit, not a rewrite.
