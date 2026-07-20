# Wyckoff Scanner & Trader Toolkit

An automated, Wyckoff-based market scanner plus a set of tools for a
**discretionary options trader**. It watches a list of names, and when a
Wyckoff-relevant event appears it pushes a "go look at this chart" alert to
Telegram — with the directional bias for a long call/put and a realized-vol
expected move for strike sanity.

> **Read this first — honesty about what this is.**
> Backtesting (see [`docs/BACKTEST_FINDINGS.md`](docs/BACKTEST_FINDINGS.md))
> showed that **none of these Wyckoff signals beat a naive "trade the swing"
> baseline** — there is **no validated mechanical edge**. So this is a
> *discretionary review aid*, not a buy/sell system. Every alert is a prompt
> for **your** judgment (context, IV, earnings, news), never an instruction.
> The tools below exist to make your discretion measurable and disciplined —
> not to automate it.

---

## What's in here

```
wyckoff-scanner-ci/
├── README.md                     <- you are here
├── requirements.txt
├── .github/workflows/            <- the automation (GitHub Actions, cron)
│   ├── sp500-scan.yml            <- daily scan of top-50 + AI names
│   ├── watchlist-scan.yml        <- daily deep-scan of the core watchlist (+charts)
│   └── tests.yml                 <- detector regression tests, runs on every push
├── src/                          <- all Python
│   ├── wyckoff_common.py         <- data fetch (Twelve Data), ATR, pivots, zigzag
│   ├── wyckoff_patterns.py       <- trading range, climax/AR, SOS/SOW, LPS/LPSY, ABC
│   ├── wyckoff_charts.py         <- annotated candlestick PNGs
│   ├── wyckoff_notify.py         <- Telegram push
│   ├── pretrade.py               <- realized-vol expected move (strike sanity)
│   ├── options_pricing.py        <- modeled long-option P&L (Black-Scholes + realized vol)
│   ├── stats_utils.py            <- bootstrap CI/p-value for the backtest's edge deltas
│   ├── survivorship_sensitivity.py  <- quantifies how fragile the baseline is to survivorship bias
│   ├── wyckoff_scanner.py        <- top-50 + AI scanner
│   ├── wyckoff_watchlist_scanner.py  <- core-watchlist deep scanner
│   ├── backtest.py               <- the honest backtest (signals vs fair baseline)
│   ├── trade_journal.py          <- log your trades (local)
│   ├── trade_analytics.py        <- measure YOUR realized edge (local)
│   └── correlation.py            <- portfolio concentration / heat monitor (local)
├── tests/
│   └── test_wyckoff_patterns.py  <- detector regression tests (synthetic OHLC fixtures)
├── data/
│   ├── core_watchlist.csv        <- the 44-name deep-scan list
│   └── top50_plus_ai.csv         <- top 50 by market cap + AI names
└── docs/
    ├── BACKTEST_FINDINGS.md       <- why there's no mechanical edge; read it
    ├── SCANNER_FLOW.md            <- end-to-end flow + every guardrail, explained
    └── diagrams/
        └── scanner_flow.html      <- interactive version of the above (open in a browser)
```

**Not committed (private / local only):** `twelvedata_api_key.txt`,
`trades.csv` (your P&L), and the generated `charts/` folder — all gitignored.

---

## The automated scanner

**How it runs:** GitHub Actions runs the two workflows on a weekday cron and
pushes results to Telegram. Nothing needs to stay open on your machine. For
the full end-to-end flow — every fetch/data/signal guardrail, with an
interactive diagram — see [`docs/SCANNER_FLOW.md`](docs/SCANNER_FLOW.md).

- `watchlist-scan.yml` — deep-scans `data/core_watchlist.csv`, generates an
  annotated chart per flagged name, and pushes a Telegram message + chart
  images. Runs ~20:35 UTC on weekdays (~16:35 ET, see DST note below).
- `sp500-scan.yml` — lighter scan of `data/top50_plus_ai.csv`. Runs ~20:20 UTC
  (~16:20 ET).

> **DST note:** GitHub cron is fixed UTC and can't itself follow US DST, so
> each workflow's `schedule:` has two `cron:` entries keyed by month — one
> daylight-time offset (Mar-Oct), one standard-time offset (Nov-Feb) — instead
> of drifting an hour off market close for half the year. There's still up to
> ~1hr of drift in the 1-2 week transition windows each March/November, since
> calendar months don't line up exactly with the actual DST switchover date.

**Signals it flags** (all discretionary review triggers): textbook spring /
upthrust, trading-range entry, Buying/Selling Climax + Automatic Reaction,
Sign of Strength / Weakness, Last Point of Support / Supply, Elliott-style
ABC correction. Each alert states a **LONG CALL / LONG PUT** bias and a ~30-day
expected move for strike/target sanity.

### One-time setup (already done on this repo, documented for portability)
1. Free API key from [twelvedata.com](https://twelvedata.com) → repo secret `TWELVEDATA_API_KEY`.
2. Telegram bot via @BotFather → repo secrets `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.
3. `gh secret set TWELVEDATA_API_KEY < twelvedata_api_key.txt` etc.

### Run a scan manually
```
gh workflow run watchlist-scan.yml        # from the repo
# or locally (needs twelvedata_api_key.txt at repo root):
python src/wyckoff_watchlist_scanner.py
```

---

## The trader tools (run locally)

These are the highest-leverage pieces — they make your discretion measurable.
They read/write `trades.csv` at the repo root, which **stays on your machine**.

### 1. Trade journal — `trade_journal.py`
Log every options trade with its decision context and outcome.
```
python src/trade_journal.py open --ticker NVDA --dir long_call --setup spring \
    --thesis "spring at 180 support, RS rising" --underlying 182.40 \
    --opt-price 4.20 --strike 190 --expiry 2026-08-15 --contracts 3 \
    --iv-rank 55 --earnings 2026-08-27          # warns if trading into earnings
python src/trade_journal.py list                # open positions
python src/trade_journal.py close --id 1 --opt-price 7.10 --reason target
```
Exit reasons: `target | stop | thesis_invalid | time_stop | earnings_exit | discretionary`

**Closing the loop with the scanner:** when you run `wyckoff_watchlist_scanner.py`
**locally** (not from the GitHub Actions cron — that runner's filesystem is
discarded when the job ends), every signal it fires auto-appends a `draft` row
here — ticker, setup, direction, thesis, and the underlying price at signal
time — so you don't transcribe it by hand. Drafts are inert until you decide to
trade one:
```
python src/trade_journal.py drafts                                   # pending signal drafts
python src/trade_journal.py promote --id 7 --opt-price 4.20 --strike 190 \
    --expiry 2026-08-15 --contracts 3                                # turn into a real position
python src/trade_journal.py discard --id 7 --notes "IV too rich"     # or drop it
```

### 2. Edge analytics — `trade_analytics.py`
Your realized edge, from your own trades (survivorship-free, honest).
```
python src/trade_analytics.py            # win rate, expectancy, profit factor,
                                          # breakdown by setup, behavioral leaks
python src/trade_analytics.py --telegram  # also push the digest
```
Surfaces leaks like *holding losers longer than winners* and *earnings drag*.

### 3. Pre-trade expected move — `pretrade.py`
```
python src/pretrade.py NVDA --horizon 30
```
Realized-vol 1-sigma move for strike/target sanity. **IV rank not included**
(needs a paid options feed) — check it in your broker before entering.

### 4. Correlation / portfolio-heat — `correlation.py`
```
python src/correlation.py                        # analyze open trades
python src/correlation.py --tickers NVDA,AMD,AVGO --account 50000
python src/correlation.py --tickers NVDA,AMD --add COST   # what-if diversification
```
Shows effective independent bets, SPY correlation, and premium at risk — so a
concentrated tech book doesn't masquerade as diversification.

---

## Research / backtest — `backtest.py`
Reproduces the honest finding. Fetches ~20y daily history for the watchlist,
tests every signal's forward return against a **fair "trade the swing"
baseline**, and reports the (lack of) edge — twice: once on the underlying's
stock return, and once on a **modeled long-option round-trip P&L**
(`options_pricing.py`: Black-Scholes with realized vol as an IV proxy, theta
decay, and an assumed bid-ask spread), since a signal can look mildly behind
on stock returns and be dramatically worse once actually priced as a call/put.
Every edge is reported with a **cluster-bootstrap 95% confidence interval and
p-value** (`stats_utils.py` — resamples by ticker, not by individual signal
instance, since same-ticker instances aren't independent draws), so a gap
like "−19pp" can be read as "real" or "noise given this sample size" rather
than taken as a bare point estimate. Read
[`docs/BACKTEST_FINDINGS.md`](docs/BACKTEST_FINDINGS.md) for the conclusions.

```
python src/backtest.py
```

## Tests
Regression tests for the pattern detectors (spring/upthrust/ABC) against
synthetic OHLC fixtures — pins down the textbook behavior that was previously
only checked by hand against one ticker. Runs automatically on every push
(`.github/workflows/tests.yml`).
```
python -m pytest tests/ -v
```

---

## Data & limits (honest)
- **Data:** Twelve Data free tier — daily bars only, no delisted names, no
  options/IV, no earnings calendar. Fine for a discretionary aid.
- **A validated mechanical edge would require paid data** (survivorship-free,
  ~$229/mo Twelve Data Pro) **and new signal ideas.** Until then, treat every
  number here as descriptive, not predictive, and trade your own judgment.
- **Single vendor, no fallback.** Both scanners depend entirely on Twelve
  Data's free tier (7 calls/min, 800/day). If that account or endpoint has an
  outage or gets rate-limited, both scans fail with no automatic failover to
  a second data source — you'd see it as a skipped/degraded run, not silent
  wrong data (`load_api_key()`/`fetch_bars()` fail loudly rather than
  returning stale or synthetic bars). Adding a real fallback (e.g. Yahoo
  Finance as a secondary source) is a bigger change than fits here — noting
  it as a known single point of failure rather than solving it.
- **Cron DST handling:** both scan workflows now split their schedule into a
  daylight-time and standard-time cron entry (keyed by month) instead of a
  single fixed-UTC time that silently drifted an hour off market close for
  half the year. There's still up to ~1hr of drift in the 1-2 week transition
  windows each March/November (calendar months don't line up exactly with
  the actual DST switchover date), but it no longer requires a manual nudge.
