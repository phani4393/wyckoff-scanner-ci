"""
Estimated long-option P&L for a directional signal -- turns the underlying's
forward return into an approximate call/put round-trip P&L, so the backtest
measures what a long-option trade would actually have made rather than just
whether the stock cooperated.

This is a MODEL, not real historical fills -- Twelve Data's free tier has no
options chain history. It uses:
  - An ATM Black-Scholes price with REALIZED volatility (trailing 20d) as the
    implied-vol input. No skew, no smile, no term structure, and no earnings
    vol crush -- if IV moves independently of realized vol, this model can't
    see it.
  - A synthetic exact-ATM strike (real chains only offer discrete strikes).
  - An assumed round-trip bid-ask spread cost, charged on both legs.

It answers a narrower, honest question than "would this exact trade have
filled at your broker": even granting a fair options price and a full
round-trip of theta decay, does the signal still make money once time and
vol are priced in -- or does it only look good when measured on the stock's
raw return.
"""

import math

import pretrade

RISK_FREE_RATE = 0.045
OPTIONS_DTE_BUFFER = 15   # extra days of time bought beyond the intended holding horizon,
                          # matching a real buyer's habit of not letting an option expire
                          # exactly on the day they plan to exit
SPREAD_PCT = 0.06         # assumed round-trip bid-ask cost as a fraction of the mid price
TRADING_DAYS_PER_YEAR = 252
MIN_ENTRY_PRICE = 0.01    # options priced below this are unrealistically cheap/illiquid to trade


def _norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_price(S, K, T, r, sigma, option_type):
    """Black-Scholes price. T in years, sigma annualized. At/after expiry
    (T<=0) or with no vol, collapses to intrinsic value."""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        intrinsic = (S - K) if option_type == "call" else (K - S)
        return max(intrinsic, 0.0)
    sqrt_t = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    if option_type == "call":
        return S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)
    return K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)


def option_pnl_pct(bars, entry_idx, horizon_days, direction,
                    dte_buffer=OPTIONS_DTE_BUFFER, spread_pct=SPREAD_PCT, r=RISK_FREE_RATE):
    """Approximate long-option round-trip P&L (fraction, e.g. 0.30 = +30%)
    for a directional signal. direction 'bullish' -> long call, 'bearish' ->
    long put. Returns None if there isn't enough trailing history to price
    the entry, the position would run past the end of the data, or the
    modeled entry price is too small to be a realistic trade."""
    exit_idx = entry_idx + horizon_days
    if exit_idx >= len(bars):
        return None

    sigma0 = pretrade.realized_daily_vol(bars[: entry_idx + 1])
    if sigma0 is None or sigma0 <= 0:
        return None
    sigma0_annual = sigma0 * math.sqrt(TRADING_DAYS_PER_YEAR)

    S0 = bars[entry_idx]["close"]
    K = S0  # synthetic exact-ATM strike
    dte_days = horizon_days + dte_buffer
    T0 = dte_days / TRADING_DAYS_PER_YEAR
    option_type = "call" if direction.startswith("bullish") else "put"

    entry_mid = bs_price(S0, K, T0, r, sigma0_annual, option_type)
    if entry_mid < MIN_ENTRY_PRICE:
        return None
    entry_paid = entry_mid * (1 + spread_pct / 2)

    sigma1 = pretrade.realized_daily_vol(bars[: exit_idx + 1]) or sigma0
    sigma1_annual = sigma1 * math.sqrt(TRADING_DAYS_PER_YEAR)
    S1 = bars[exit_idx]["close"]
    remaining_days = dte_days - horizon_days
    T1 = remaining_days / TRADING_DAYS_PER_YEAR

    exit_mid = bs_price(S1, K, T1, r, sigma1_annual, option_type)
    exit_received = exit_mid * (1 - spread_pct / 2)

    return (exit_received - entry_paid) / entry_paid
