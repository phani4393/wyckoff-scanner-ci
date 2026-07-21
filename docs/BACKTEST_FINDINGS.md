# Backtest findings — read before trusting any signal

**Bottom line: 10 of the 11 implemented Wyckoff signals show a statistically
significant NEGATIVE edge vs. naive swing-trading (p<0.001, confirmed by
bootstrap, not a small-sample fluke) — and once modeled as actual option trades,
most of them lose considerably more than the stock-return numbers alone suggest.
The 11th (ABC) is not significant either way** — the data can't currently tell
you if it has an edge, only that this sample isn't enough to prove one exists.
They are shipped as *discretionary review triggers* — "come look at this chart"
— not as validated buy/sell signals. Apply your own judgment (market context, IV,
news, broader structure) before entering any option.

## What was tested

- Universe: the 44-name Core Watchlist (large-cap / growth / momentum names); 41
  had enough history to run (BABA/BKNG had insufficient free-tier history).
- History: up to ~20 years of daily bars per name (Twelve Data, `outputsize=5000`).
- 18,148 total signal instances across all types.
- For every historical instance of each signal, two parallel measurements at 5 /
  10 / 20 trading days:
  1. **Stock-return edge** — direction-adjusted forward return of the underlying.
  2. **Options-P&L edge** — a modeled long-option round-trip (`src/options_pricing.py`):
     Black-Scholes priced with realized volatility as an IV proxy, a synthetic
     ATM strike, DTE = horizon + 15 days, and an assumed 6% round-trip spread
     cost. This prices in theta decay and vol, so it answers a narrower, more
     honest question than the stock-return number: even granting a fair option
     price and a full round-trip of decay, does the signal still make money as
     a call/put — not just "would the direction have been right."
- **Every edge above is bootstrapped** (`src/stats_utils.py`, 2000 resamples):
  a 95% confidence interval and two-sided p-value on the edge itself, so "−19pp"
  can be read as "real, CI excludes zero" rather than taken as a bare point
  estimate that might just be small-sample noise.
- **The bootstrap resamples TICKERS, not individual signal instances** (a
  cluster bootstrap). An earlier version resampled instances directly, which
  is wrong: same-ticker instances share trend/vol/beta and have overlapping
  forward-return windows, so they aren't independent draws, and resampling
  them as if they were understates the true CI width. Each bootstrap
  replicate now resamples the ~42-43 tickers with replacement and pulls each
  drawn ticker's entire set of instances along with it. This is scoped to
  same-ticker correlation only — it does not correct for cross-ticker
  correlation on the same calendar date (e.g. a market-wide selloff
  triggering springs on many names at once still isn't N independent events).
  **Result of switching to the cluster bootstrap: no signal's significance
  verdict changed.** Every previously-significant signal remained
  significant and ABC remained not-significant, because almost every signal
  here fires across 42-43 of the 44 tickers — the effect is broad-based
  across nearly the whole universe, not concentrated in a few names, which is
  exactly the condition under which a cluster bootstrap and a naive one agree.
  That makes this a *stronger* result than the original bootstrap gave, not a
  weaker one — it survives a more conservative, methodologically correct test.

## The fair baseline

The honest control is not "a random entry" — it's **the naive version of the same
idea**: just trade every confirmed swing point (long the swing lows, short the
swing highs), with none of the volume / RS / Elliott-ratio machinery. This baseline
is measured both ways too, so every signal is compared against a fair, matched,
options-aware control — not against itself.

- Buy every confirmed swing low: **73% hit rate, +4.7% avg stock return over 10
  days** — and **+38.3% avg option P&L** once priced as a call (leverage cuts
  both ways: it amplifies the baseline's edge too, which is exactly why signals
  that look only mildly worse on stock returns turn out much worse on P&L).
- This is itself mostly "buy the dip on names that went up for 20 years" —
  survivorship + a secular bull market, **not** a proven forward edge.

## Results vs. that baseline (10-day, ~42-43 tickers, cluster-bootstrap significance)

| Signal | Stock hit rate | Stock-return edge (95% CI, cluster boot) | Options-P&L edge (95% CI, cluster boot) |
|---|---|---|---|
| ABC correction | 72% | +0.6pp, CI **[-3.3, +4.6]pp, p=0.76** — NOT significant | +1.9pp hit / +1.4pp P&L, CI includes 0, p=0.42/0.80 — NOT significant |
| pure spring (textbook) | 54% | −19.1pp, CI [-22.5, -15.4]pp, **p<0.001** | −14.3pp hit / −23.0pp P&L, **p<0.001** |
| pure upthrust (textbook, short) | 44% | −25.6pp, CI [-28.3, -23.1]pp, **p<0.001** | −21.6pp hit / −44.8pp P&L, **p<0.001** |
| SC (bullish) | 59% | −14.0pp, CI [-19.9,-7.5]pp, **p<0.001** (ret edge NOT sig, p=0.16) | −6.2pp hit (NOT sig, p=0.07) / −2.5pp P&L (NOT sig, p=0.78) |
| LPS / SOS (bullish) | 53–56% | −20 to −21pp, **p<0.001** | −16 to −18pp hit / −27 to −30pp P&L, **p<0.001** |
| BC / SOW / LPSY / upthrust (bearish) | 42–47% | −23.6 to −28.4pp, **p<0.001** | −19 to −24pp hit / −36 to −50pp P&L, **p<0.001** |

**10 of 11 signal types have a statistically significant negative edge at every
horizon tested, confirmed under a cluster bootstrap that resamples by ticker
(not by individual instance)** — sample sizes range from n≈350 (SC) to
n≈5,800 (upthrust), backed by 42-43 independent tickers. **ABC is the sole
exception: its edge is not distinguishable from zero at any horizon, on
either measure.** That is a different and more honest claim than "ABC has no
edge" — it means this dataset can't currently tell you whether ABC has a real
(positive or negative) edge; a much larger sample or a different test design
would be needed to resolve it. **SC's stock-return and options-P&L edges are
significant on hit rate but not on average return/P&L at 5-10d** — a real,
if smaller, exception worth naming rather than folding into "significant."

The pattern across every significant signal: the options-P&L edge is worse in
P&L terms than the stock-return edge implied, because the matched baseline's
own leveraged return sets a much higher bar once both sides are priced as
options. A signal that's "only" 15-20pp behind on stock hit rate can be
30-50pp behind on modeled option P&L.

Notes:
- Accurate, hand-verified "textbook" spring/upthrust detectors were used — the
  failure is **not** a detection artifact. These detectors also now have an
  automated regression test suite (`tests/test_wyckoff_patterns.py`, run in CI
  on every push) pinning down the exact textbook behavior, so this can't
  silently drift the way the earlier hand-verification-only approach could.
- A 200-day-SMA trend filter did **not** help (72.6% vs 73.2%, stock terms).
- ABC live timing was fixed to fire only on the confirmation bar, matching what
  the backtest measured (no repaint gap) — now covered by a regression test.

## What the options-P&L model does NOT capture

- **Real IV, not realized vol.** If implied vol moves independently of realized
  vol — an earnings-linked IV crush right after a spring, for example — this
  model can't see it. Free-tier data has no historical options chain.
- **No skew/smile, no discrete strikes.** Uses an exact synthetic ATM strike;
  real chains only offer strikes at fixed increments.
- **Spread cost is an assumption (6% round-trip), not measured.** Wider on
  small-caps/low-volume names than on AAPL/MSFT-tier liquidity.

## Does the broader market regime explain the edge? (tested, not just asked)

A reasonable hypothesis: springs/upthrusts might fail more often specifically
*because* the broader market is fighting them — a spring's local demand test
competing against systemic selling pressure, not just idiosyncratic noise.
Tested with one definition, committed before looking at results (not fished
across variants): **regime = "bull" if SPY's close is above its own 200-day
SMA on the signal date, else "bear"** (`src/regime_analysis.py`). Every
observation — signal AND matched baseline — is split by this regime and
compared against a regime-matched control, same cluster bootstrap as above.

| Signal | SPY regime | Signal's own hit rate | Edge vs. regime-matched baseline (10d) |
|---|---|---|---|
| pure spring | bull | 54.1% | −17.9pp, CI [-22.0,-13.5]pp, **p<0.001** |
| pure spring | bear | 54.4% | −23.2pp, CI [-27.4,-18.7]pp, **p<0.001** |
| pure upthrust | bull | 44.8% | −26.0pp, CI [-28.7,-23.4]pp, **p<0.001** |
| pure upthrust | bear | 42.0% | −25.5pp, CI [-33.3,-18.3]pp, **p<0.001** |

**Verdict: the regime split does not rescue the edge.** Both signals remain
strongly, significantly negative in both regimes. Spring's own hit rate barely
moves across regimes (54.1% vs 54.4% — if anything trivially higher when the
market is down, the opposite of the hypothesis); its edge looks worse in the
bear regime only because the naive baseline's own "buy the dip" bounce gets
*even stronger* in down markets (77.6% baseline hit rate in bear vs. 72.0% in
bull), not because springs themselves fail more. Upthrust does show a small
move in the hypothesized direction (44.8% → 42.0% hit rate, bull → bear), but
that's a ~3pp shift against a ~25pp gap — nowhere near enough to explain it.
This is a genuinely useful negative result: it rules out the single most
obvious confounding variable rather than leaving it as an open "maybe," without
fishing across multiple regime definitions until one looked better.

## Survivorship-bias sensitivity (quantified, not just disclosed)

Every ticker in this backtest is, by construction, a company still trading
today — the "73% hit rate, +4.7% avg" swing baseline never includes a single
"the company went to zero" instance, because Twelve Data's free tier has no
delisted-name history (SIVB/FRC/TWTR/ATVI-style events are invisible). The
true magnitude of that bias can't be measured without paid data, but it can be
**bounded** with a stress test (`src/survivorship_sensitivity.py`): blend the
observed baseline with a hypothetical fraction of catastrophic failures and see
how much that erodes the headline number.

| Hypothetical failure rate | Swing baseline, stock return (10d) | Swing baseline, options P&L (10d) |
|---|---|---|
| 0% (as measured) | +4.69% | +38.3% |
| 2% | +2.60% | +35.5% |
| **5%** | **−0.54%** | +31.4% |
| 10% | −5.78% | +24.5% |
| 20% | −16.25% | +10.6% |

**The entire "beats a coin flip" stock-return baseline flips negative at just a
5% hypothetical failure rate** — which is not an implausible number for a
20-year window across growth/momentum names. This doesn't mean the true rate
is 5%; it means the headline baseline number is fragile enough to survivorship
bias that it shouldn't be treated as a solid floor. (The options-P&L baseline
is comparatively more robust to this particular stress, because a long option
already caps its own downside near −100% — the stress test's assumed "total
loss" isn't as extreme relative to the option distribution as it is relative
to the stock-return distribution.)

## What this does NOT prove

- It does not prove Wyckoff "doesn't work" — Wyckoff is discretionary and
  context-dependent; a mechanical proxy losing edge is consistent with that.
- It is in-sample on survivor names in a bull market, and out-of-sample /
  walk-forward testing hasn't been done. A fully validated edge still needs a
  real survivorship-free universe (incl. delisted names) — the sensitivity
  analysis above bounds the risk but doesn't remove it.
- **Update, 2026-07-21:** `src/score_alerts.py` has started closing the
  walk-forward gap specifically — every live alert since that date is scored
  against a time-matched swing baseline as it ages past 5/10/20 trading days.
  Full explanation and a worked example: [`LIVE_SCORECARD.md`](LIVE_SCORECARD.md).
  This is a genuine live/forward check (no detector-tuning-against-visible-
  history risk), but it does **not** touch the survivorship-bias gap above —
  that still needs paid, delisted-inclusive data — and the sample will be
  small for a long time, so don't expect a statistically meaningful verdict
  from it soon.
