# Scanner flow — end to end, with guardrails

An interactive breakdown of exactly what happens on every scan run, and every
check/guardrail along the way.

**[Open the interactive diagram](diagrams/scanner_flow.html)** — click any
stage to expand its detail; the per-ticker loop expands into five sub-steps of
its own. GitHub's file viewer won't execute the JS inline, so download the
repo (or clone it) and open `docs/diagrams/scanner_flow.html` directly in a
browser to interact with it.

## The five stages

1. **Trigger** — GitHub Actions cron (DST-aware: split into a daylight-time
   and standard-time schedule keyed by month, so it doesn't drift an hour off
   market close for half the year), a manual `workflow_dispatch`, or a local
   run (`python src/wyckoff_watchlist_scanner.py`).

2. **Setup**
   - API key: `TWELVEDATA_API_KEY` env var first (a GitHub secret in CI),
     falls back to the local gitignored `twelvedata_api_key.txt`. Fails
     loudly if neither exists.
   - Loads the ticker list (`data/core_watchlist.csv`, 44 names, or
     `data/top50_plus_ai.csv`).
   - Fetches SPY as the RS benchmark. **Guardrail:** if that fetch fails, the
     whole scan aborts immediately — no partial run against a broken benchmark.

3. **Per-ticker scan loop** — runs once per ticker, five sub-steps:
   - **Rate limiter** (guardrail) — max 7 calls/minute against Twelve Data's
     8-credit/minute free-tier cap; a rolling window throttle sleeps rather
     than risk a 429.
   - **Fetch bars** (processing) — retries twice with a 1s pause on
     network/timeout errors; a 10s backoff + retry on HTTP 429; returns
     `None` immediately (no retry) on a bad symbol or empty response.
   - **Data checks** (guardrail) — `drop_unconfirmed_last_bar()` discards the
     newest bar if its volume is under 10% of the trailing 20-day average
     (the session likely hasn't settled). Fewer than 60 bars of history and
     the ticker is skipped outright.
   - **Run detectors** (processing) — Wilder ATR-14, pivot highs/lows over a
     20-left/5-right bar window, and zigzag swings feed: textbook
     spring/upthrust (undercut-and-recover / poke-and-fail), Buying/Selling
     Climax + Automatic Reaction (2x volume spike **and** 1.8x ATR range
     spike), Sign of Strength/Weakness (1.5x volume, 1.3x range, close beyond
     the level), Last Point of Support/Supply (pullback holding within 0.6x
     ATR of the broken level), and an Elliott-style ABC correction (leg-B
     retrace 30-79% of leg A, leg-C extension 61.8-161.8%).
   - **Signal guardrails** (guardrail) — every detector is edge-triggered
     (fires only the day a condition becomes true, not every day it stays
     true). ABC fires *only* on its exact confirmation day
     (`pivot_idx + RIGHT_BARS`), never earlier — the anti-repaint fix for a
     real lookahead bug found during backtesting, now pinned down by a
     regression test (`tests/test_wyckoff_patterns.py`).

4. **Aggregate results** — if more than half the tickers failed to fetch,
   that alone triggers a "scan degraded" Telegram message, separate from any
   signal alert. If nothing fired, the scan exits quietly.

5. **Notify and archive** — every alert carries the same disclaimer:
   *"discretionary review triggers, NOT validated edges — backtesting shows
   none beat naive swing-trading."* That's load-bearing: the backtest
   confirmed 10 of 11 signal types have a statistically significant
   **negative** edge (cluster-bootstrapped, p<0.001; see
   [`BACKTEST_FINDINGS.md`](BACKTEST_FINDINGS.md)). The watchlist scanner also
   generates an annotated chart PNG per signal, uploads it as a 14-day GitHub
   Actions artifact, and — only on local runs, guarded by a `GITHUB_ACTIONS`
   env var check — auto-drafts a `trades.csv` row.

## Guardrails that don't show up as boxes

- Secrets never touch git — `.gitignore` covers the API key file and
  `trades.csv`.
- No fallback data vendor if Twelve Data has an outage — a documented single
  point of failure, not silently masked.
- The entire "review trigger" framing exists because the backtest, not
  intuition, proved there's no mechanical edge to trust blindly.
