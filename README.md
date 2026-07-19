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
│   └── watchlist-scan.yml        <- daily deep-scan of the core watchlist (+charts)
├── src/                          <- all Python
│   ├── wyckoff_common.py         <- data fetch (Twelve Data), ATR, pivots, zigzag
│   ├── wyckoff_patterns.py       <- trading range, climax/AR, SOS/SOW, LPS/LPSY, ABC
│   ├── wyckoff_charts.py         <- annotated candlestick PNGs
│   ├── wyckoff_notify.py         <- Telegram push
│   ├── pretrade.py               <- realized-vol expected move (strike sanity)
│   ├── wyckoff_scanner.py        <- top-50 + AI scanner
│   ├── wyckoff_watchlist_scanner.py  <- core-watchlist deep scanner
│   ├── backtest.py               <- the honest backtest (signals vs fair baseline)
│   ├── trade_journal.py          <- log your trades (local)
│   ├── trade_analytics.py        <- measure YOUR realized edge (local)
│   └── correlation.py            <- portfolio concentration / heat monitor (local)
├── data/
│   ├── core_watchlist.csv        <- the 44-name deep-scan list
│   └── top50_plus_ai.csv         <- top 50 by market cap + AI names
└── docs/
    └── BACKTEST_FINDINGS.md       <- why there's no mechanical edge; read it
```

**Not committed (private / local only):** `twelvedata_api_key.txt`,
`trades.csv` (your P&L), and the generated `charts/` folder — all gitignored.

---

## The automated scanner

**How it runs:** GitHub Actions runs the two workflows on a weekday cron and
pushes results to Telegram. Nothing needs to stay open on your machine.

- `watchlist-scan.yml` — deep-scans `data/core_watchlist.csv`, generates an
  annotated chart per flagged name, and pushes a Telegram message + chart
  images. Runs ~20:35 UTC on weekdays.
- `sp500-scan.yml` — lighter scan of `data/top50_plus_ai.csv`. Runs ~20:20 UTC.

> **DST note:** GitHub cron is fixed UTC. The times above line up with ~market
> close during US daylight time; in US winter they drift ~1 hour. Nudge the
> `cron:` lines by +1 hour around early November if you want them re-pinned.

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
baseline**, and reports the (lack of) edge. Read
[`docs/BACKTEST_FINDINGS.md`](docs/BACKTEST_FINDINGS.md) for the conclusions.

```
python src/backtest.py
```

---

## Data & limits (honest)
- **Data:** Twelve Data free tier — daily bars only, no delisted names, no
  options/IV, no earnings calendar. Fine for a discretionary aid.
- **A validated mechanical edge would require paid data** (survivorship-free,
  ~$229/mo Twelve Data Pro) **and new signal ideas.** Until then, treat every
  number here as descriptive, not predictive, and trade your own judgment.
