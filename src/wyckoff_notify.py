"""
Telegram notification for the headless GitHub Actions runs -- there's no
Claude session in that loop to call PushNotification, so the scripts send
their own notification directly via the Telegram Bot API.

Requires two repo secrets, exposed as env vars by the workflow:
  TELEGRAM_BOT_TOKEN -- from @BotFather
  TELEGRAM_CHAT_ID   -- your personal chat id (see setup instructions)

Unlike PushNotification, sendPhoto actually delivers the chart image itself
to your phone, not just a file path.
"""

import os

import requests

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
API_BASE = "https://api.telegram.org/bot{token}/{method}"


def _configured():
    return bool(BOT_TOKEN and CHAT_ID)


def send_message(text):
    if not _configured():
        print("Telegram not configured (missing TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID) -- skipping notification.")
        return
    url = API_BASE.format(token=BOT_TOKEN, method="sendMessage")
    resp = requests.post(url, data={"chat_id": CHAT_ID, "text": text}, timeout=20)
    if not resp.ok:
        print(f"Telegram sendMessage failed: {resp.status_code} {resp.text}")


def send_photo(path, caption=""):
    if not _configured():
        return
    url = API_BASE.format(token=BOT_TOKEN, method="sendPhoto")
    with open(path, "rb") as f:
        resp = requests.post(
            url,
            data={"chat_id": CHAT_ID, "caption": caption[:1024]},
            files={"photo": f},
            timeout=30,
        )
    if not resp.ok:
        print(f"Telegram sendPhoto failed for {path}: {resp.status_code} {resp.text}")


def notify_signals(header, tickers_and_lines, chart_paths=None):
    """header: one-line summary. tickers_and_lines: list of (sym, [line, ...]).
    chart_paths: optional {sym: path} to send as photos with captions."""
    if not tickers_and_lines:
        return
    body_lines = [header, ""]
    for sym, lines in tickers_and_lines:
        body_lines.append(sym + ":")
        for line in lines:
            body_lines.append("  - " + line)
    send_message("\n".join(body_lines)[:4000])  # Telegram message cap is 4096 chars

    for sym, lines in tickers_and_lines:
        path = (chart_paths or {}).get(sym)
        if path:
            send_photo(path, caption=f"{sym}: " + "; ".join(lines)[:900])


def notify_signals_tiered(header, actionable_signals, watchlist_signals=None, chart_paths=None):
    """
    Send notifications with separate sections for actionable vs watchlist-only signals.
    
    Args:
        header: one-line summary
        actionable_signals: list of (sym, tier_label, [line, ...]) for regime-aligned signals
        watchlist_signals: list of (sym, [line, ...]) for regime-filtered signals (optional)
        chart_paths: optional {sym: path} to send as photos
    
    The message format:
    
    ✅ ACTIONABLE ALERTS:
    ⭐⭐⭐ AAPL:
      - Spring at support... -- bullish
    ⭐⭐ MSFT:
      - ABC correction... -- bullish
    
    📋 WATCHLIST (regime-filtered, monitor only):
    NVDA:
      - Upthrust at resistance... -- bearish setup forming, but SPY too strong
    """
    if not actionable_signals and not watchlist_signals:
        return
    
    body_lines = [header, ""]
    
    # Sort actionable signals by tier (HIGH first, then MEDIUM, then REVIEW)
    tier_order = {"⭐⭐⭐": 0, "⭐⭐": 1, "⭐": 2}
    if actionable_signals:
        actionable_sorted = sorted(actionable_signals, key=lambda x: tier_order.get(x[1], 99))
        
        body_lines.append("✅ ACTIONABLE ALERTS:")
        for sym, tier_label, lines in actionable_sorted:
            body_lines.append(f"{tier_label} {sym}:")
            for line in lines:
                body_lines.append("  - " + line)
        body_lines.append("")
    
    # Watchlist section (regime-filtered signals)
    if watchlist_signals:
        body_lines.append("📋 WATCHLIST (regime-filtered, monitor only):")
        for sym, lines in watchlist_signals:
            body_lines.append(f"{sym}:")
            for line in lines:
                # Remove [REGIME-FILTERED] prefix if present, clean up the line
                clean_line = line.replace("[REGIME-FILTERED] ", "")
                body_lines.append("  - " + clean_line)
    
    send_message("\n".join(body_lines)[:4000])
    
    # Send charts only for actionable signals
    for sym, tier_label, lines in (actionable_signals or []):
        path = (chart_paths or {}).get(sym)
        if path:
            send_photo(path, caption=f"{tier_label} {sym}: " + "; ".join(lines)[:850])
