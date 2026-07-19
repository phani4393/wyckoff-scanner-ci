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
