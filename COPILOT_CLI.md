# Copilot CLI Runbook

Use this repo in short, resumable 5-minute iterations.

## Goal

Keep scraping ONPE mesas in small batches, update `output/` incrementally, and push only when there are meaningful data changes.

## Loop

1. Run the scraper in mesas mode with a short time cap.
   ```powershell
   python -m src.onpe_scraper.main --modo mesas --tiempo-max 4 --max-workers 5 --batch-size 200
   ```
2. Inspect what changed.
   ```powershell
   git status --short
   git diff --stat
   ```
3. If there are data updates, commit only the data/state files.
   ```powershell
   git add output/ work/mesas_pendientes.txt
   git commit -m "data: <UTC_TIMESTAMP> — pendientes: <N> mesas"
   git push
   ```
4. Repeat every 5 minutes.

## Resumable rule

- If `work/mesas_pendientes.txt` still has lines, the next run resumes from those mesas.
- If it is empty, stop the mesa loop until ONPE publishes more contabilizadas or until you explicitly want a bootstrap run.

## What to push

- Push `output/*.txt` when they changed.
- Push `work/mesas_pendientes.txt` so the next run resumes correctly.
- Do not push transient scratch files or one-off debug artifacts.

## What not to do

- Do not commit a run if it only produced no-op output.
- Do not re-run immediately after a successful push unless you are intentionally continuing the 5-minute loop.
- Do not add GitHub Actions back unless you want automated cloud scheduling again.

## Suggested Copilot CLI prompt

Use this prompt when you want Copilot CLI to drive the cycle:

> Run the ONPE scraper in mesas mode with a 4-minute cap, keep the run resumable, inspect the diff, and if output files changed commit only `output/` and `work/mesas_pendientes.txt` with a UTC timestamped data commit, then `git push`. Repeat every 5 minutes and stop if there are no meaningful data changes.
