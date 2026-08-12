# AI Session Summary — 2026-08-12

Notes from a research session on this project, written to be handed to another
AI system (or a person) as context without needing the original conversation.
If you're an AI picking this up cold: read this file, then `README.md`, then
`docs/BACKTEST_FINDINGS.md` for full technical detail — this file summarizes
and links rather than duplicating either.

## What this project is

A Wyckoff-pattern market scanner (`src/wyckoff_scanner.py`,
`wyckoff_watchlist_scanner.py`) that pushes Telegram alerts when a chart
pattern fires, plus a research/backtest track (`src/backtest.py`,
`docs/BACKTEST_FINDINGS.md`) that tests whether those patterns actually
predict profitable trades, plus a set of local trading tools (trade journal,
edge analytics, pre-trade sizing, correlation/portfolio-heat). Full
architecture: `docs/SYSTEM_FLOW.md`.

## The core finding (already established before this session)

`docs/BACKTEST_FINDINGS.md` is the authoritative source. Headline: **10 of the
11 implemented Wyckoff signals have a statistically significant NEGATIVE edge
vs. a naive "trade the swing" baseline** (p<0.001, cluster-bootstrapped by
ticker, not a small-sample artifact) — mechanically trading these signals
loses to just buying every dip / shorting every rally with no pattern logic
at all. The 11th signal, ABC correction, was inconclusive (not enough data to
say either way). This was already backed by a pre-committed market-regime
test (ruled out "maybe it only fails in down markets") and a quantified
survivorship-bias sensitivity check before this session started.

**Practical implication, stated plainly:** the Telegram alerts this system
sends are validated as a *discretionary review trigger* ("go look at this
chart") — never as a mechanical buy/sell instruction. If an alert firing is
the reason a trade gets opened, that is the exact behavior the data shows
loses money. The pattern detection itself is accurate (regression-tested
against textbook definitions); the failure is that "pattern occurred" doesn't
predict "profitable trade."

## What this session added

**1. Expanded the backtest's ticker universe to test robustness.** The
original backtest only ever ran against the 44-name Core Watchlist
(`data/core_watchlist.csv`), which skews large-cap growth/momentum. A second
list, `data/top50_plus_ai.csv` (59 names, skews toward broad S&P leadership —
financials, industrials, healthcare, staples), existed in the repo but had
never been run through `backtest.py`. Added an `--extra-tickers` flag to
`src/backtest.py` to union in a second ticker file without changing default
behavior or overwriting the original results (`backtest_results.json`
untouched; the expanded run writes to `backtest_results_expanded.json`, both
gitignored).

Ran it: 82-83 tickers (up from ~41-44), 43,199 signal instances (up from
18,148).

**Result — robustness confirmed:** all 10 previously-significant signals
stayed significant, same direction, similar magnitude (e.g. pure_spring's
10-day hit edge: −19.1pp originally vs. −18.5pp expanded; pure_upthrust:
−25.6pp vs. −23.1pp). This rules out "maybe the original 44-name watchlist
just happened to be a bad sample" as an explanation for the negative finding.

**Result — ABC still inconclusive, but more informatively:** more data
narrowed its confidence interval toward zero rather than toward significance
(10-day return edge CI: was [-3.3, +4.6]pp, now [-0.3, +0.7]pp). That means if
ABC has any real edge, this data says it's small — not that the question is
simply unresolved due to a small sample. Full numbers and methodology written
into `docs/BACKTEST_FINDINGS.md` under "Does a different ticker universe
change the answer?".

**2. Fixed a local environment issue, unrelated to the code.** The first
attempt to run the expanded backtest failed immediately with `SSL:
CERTIFICATE_VERIFY_FAILED` — a corporate TLS-inspection root CA that Python's
bundled `certifi` list doesn't trust by default (confirmed systemic: even a
plain request to google.com failed the same way; not specific to Twelve Data
or this script). Fixed by installing `pip-system-certs`, which makes Python
defer to the Windows OS trust store instead of `certifi`'s bundled list — the
correct fix, not a `verify=False` downgrade. **This is a persistent change to
the local Python environment** (affects all future Python HTTPS calls on this
machine), not something scoped to just this script.

**3. Verified nothing broke.** All 34 existing regression tests
(`python -m pytest tests/ -v`) pass after the `backtest.py` change.

## If considering paid data to improve the research further

Researched during this session, not yet acted on. Two distinct gaps exist,
and they matter differently:

- **Real historical options data** (fixes the shakier part of the *options*-
  P&L numbers specifically — currently modeled via Black-Scholes with
  realized volatility standing in for real IV, a synthetic ATM strike, and an
  assumed 6% spread). **ORATS** is the purpose-built tool: near-end-of-day
  historical options data back to 2007, real IV/strikes/quotes, $99–299/mo
  depending on depth (their 1-minute intraday tier costs extra and is
  unnecessary for a daily-bar backtest). **Polygon.io** (recently rebranded —
  `polygon.io` now redirects to `massive.com`) is a broader alternative,
  ~$29/mo+ for stocks with options as a separate subscription; worth it
  mainly if also replacing Twelve Data as a second equities source (the
  README's own "single vendor, no fallback" risk).
- **Survivorship-bias-free stock data** (delisted/bankrupt names, currently
  absent — every ticker in the backtest is, by construction, a company still
  trading today). **Norgate Data** is the standard tool: 50,000+ listed and
  delisted US tickers back to 1950, stores locally, has a Python package.
  Current pricing wasn't confirmed — check norgatedata.com directly. Lower
  priority than the options-data gap: because both the signal and its matched
  baseline are drawn from the same survivor-only universe, the *relative*
  comparison (which is what "does the signal beat the baseline" actually
  rests on) is less exposed to this bias than either side's absolute number
  is.

**The more important point:** paying for either would sharpen the *research*
(a more trustworthy verdict on ABC, a more precise size on the options
losses) — it would not change the core finding that 10 of 11 signals lose
mechanically. That's not a data-quality problem. If the actual goal is better
*trading* rather than a more precise paper, the higher-value and free lever is
using tools already built and currently unused: `src/trade_journal.py` had
zero logged trades as of this session, so there is no data yet on whether
real discretion (applied on top of alerts) performs any differently than the
raw mechanical signal. That number is the one that would actually answer "can
I trust this system," personally, and it costs nothing.

## Open items / suggested next steps

- Log real trades via `trade_journal.py` as they happen — this is the single
  highest-value unresolved thread.
- The live, out-of-sample scorecard (`score_alerts.py`, running since
  2026-07-21) needs more time before its sample is large enough to mean
  anything — check back periodically, don't expect a verdict soon.
- ABC's edge remains open; revisit if the live scorecard accumulates enough
  ABC instances, or if real options data (see above) becomes available to
  replace the modeled P&L.
- Repo has uncommitted changes as of this session: `src/backtest.py`,
  `.gitignore`, `docs/BACKTEST_FINDINGS.md`, plus this file. Nothing was
  committed — left for manual review.
