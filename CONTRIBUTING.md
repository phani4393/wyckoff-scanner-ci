# Contributing — conventions this repo has actually built up

This isn't a generic template. Every rule below exists because something
specific happened that made it worth writing down — this repo is edited by
more than one Claude Code session (sometimes concurrently), so the
conventions live here instead of only in one person's head.

## The one rule everything else follows

**This is a discretionary review aid, not a trading system, and the docs
say so up front.** `BACKTEST_FINDINGS.md` leads with the negative finding,
not a marketing pitch. If you add a feature that could read as "this signal
works," it needs the same treatment: real numbers, a fair baseline, and an
honest "what this does NOT prove" section. Don't round up a result to sound
better than it is — that's the one thing this project can't recover its
credibility from if it slips.

## Before you touch anything

- **`git fetch` and check for new commits before you start, and again right
  before you push.** This repo has been edited by more than one session in
  parallel more than once — including two independent implementations of
  the same feature built the same day (`score_alerts.py` vs. an
  `alert_review.py` that got reconciled into it). Don't assume the state you
  last saw is still current.
- **Verify a theory against reality before shipping a fix built on it.** The
  `.gitattributes` union-merge fix for concurrent alert-log commits only
  happened because the first theory ("it's a rebase-vs-merge quirk") got
  tested with an actual git reproduction and found wrong. If you're about to
  fix something based on a plausible-sounding mechanism, reproduce the
  failure first.

## Code

- **One source of truth for detector logic — never reimplement.** The exact
  same spring/upthrust condition used to be written out independently in
  four places (both scanners, the backtest, and the test fixtures) before
  being consolidated into `wyckoff_common.is_pure_spring()` /
  `is_pure_upthrust()`. If you need the same check in a new place, import
  it; don't retype the boolean expression.
- **Every alert-log function returns whether it actually acted.**
  `alert_log.log_alert()` returns `False` on a same-day duplicate so
  callers (like the local trade-journal draft step) can skip their own
  downstream action too, instead of silently double-counting.
- **New data-writing scripts that run in CI need to be idempotent** (a
  dedup key on whatever they append — see `score_pending()`,
  `check_pending()`) and their workflow needs a "Persist" step that commits
  the result back to the repo, since the runner's filesystem is discarded
  when the job ends.
- **If two workflows can append to the same file, it needs `merge=union` in
  `.gitattributes`.** Confirmed necessary, not theoretical: tickers that
  appear in both `core_watchlist.csv` and `top50_plus_ai.csv` get
  independently flagged by both scanners on the same real event.
- **Telegram-sending scripts get a `--dry-run` flag.** Preview the exact
  message without the network call, so testing changes locally can't
  accidentally push to someone's real phone.

## Tests

- Every new module ships with tests in the same commit, not a follow-up.
- No network calls in tests — monkeypatch the one fetch/HTTP boundary
  (`wyckoff_common.fetch_bars`, `requests.get`, etc.) and drive it with
  synthetic data.
- If a test is checking behavior against an external API's response shape
  (like `earnings_calendar.py`'s Yahoo lookup), use a captured **real**
  response, not a guessed one.
- Regression-test the thing that already broke once. `tests/test_wyckoff_patterns.py`
  exists because detector correctness used to be checked by eyeballing one
  ticker by hand.

## Docs

- **README.md is the minimum bar for every new file** — a line in the tree
  listing, a short usage section.
- **A bigger, novel, statistically-loaded feature gets its own `docs/*.md`
  walkthrough** — a real worked example (not a hypothetical), explicit
  limitations, written so a newcomer with zero context can follow it. Smaller
  utilities (another local trader tool, a config tweak) don't need a
  separate doc — a README section is enough.
- **`docs/SYSTEM_FLOW.md` and `docs/diagrams/system_flow.html` are meant to
  track the actual system, and `tests.yml` checks that they do.** A
  `FLOW_PATHS` allowlist in the "Check system flow diagram freshness" step
  warns (doesn't block) if a flow-relevant file changes without either of
  those changing in the same push. If you add a new flow-relevant script or
  workflow file, **add it to that allowlist too** — it has a real blind spot
  for anything added since the list was last updated, which is exactly how
  it missed `alert_followup.py` for one push.

## Privacy

`trades.csv`, `twelvedata_api_key.txt`, and the generated `charts/` folder
are gitignored on purpose — personal P&L and secrets never leave your
machine. Don't add a workflow step or script change that would commit any
of them, even indirectly (e.g., a debug print that logs the API key).
