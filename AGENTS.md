# Standing rules for the agent in this project

## Never open browser pages or upload without an explicit request

- Do not open, visit, navigate, or screenshot any web page or browser tab, and do not start any upload, auto run, or publish, unless the user explicitly asks for that action in the current message.
- Do not restart the dashboard server, and do not start any script that drives the browser (`analytics.refresh`, `_seed_ledger`, the hourly `_auto_loop`, or anything under `scripts/` that uses Playwright or CDP), unless the user explicitly asks.
- Running tests, reading files, and editing code are allowed. Tests must not launch a browser or touch the user's accounts.
- If a browser page opens unexpectedly, stop, report it, and wait for the user. Do not fix it by opening more pages.

## Always push finished changes to GitHub

- When any change is finished (code, fixes, config, docs), commit it and push to GitHub (`origin`, branch `main`) before reporting it as done. Do this without being asked each time.
- Stage only the files that this work changed, by name. Never use `git add -A` or `git commit -a`, and never commit or push files you did not change: run logs under `artifacts/`, `published_models.json`, or other pending edits.
- If the commit or push fails, say so. Do not report the work as done.

## Known cause (fixed)

- Earlier, `main.py` started the hourly `analytics._auto_loop` and ran `analytics.refresh()` at every startup, which drove the user's logged-in Chrome. Both are now disabled: startup only seeds the ledger from files on disk. The account is read only when the user clicks Refresh (`POST /analytics/refresh`). Do not re-enable the automatic reads.
