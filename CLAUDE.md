# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Scanfor_Red analyzes screenshots of a "VM log alert matrix" dashboard and reports every **red/critical** cell (yellow warnings are deliberately ignored). It runs as both a CLI batch processor and a Flask web app, and ships as a Docker image bundling the Tesseract OCR engine.

## Commands

```bash
# CLI: scan one image or the whole Dashboard_Screenshot/ folder
python scanfor_red.py                       # process every new screenshot in Dashboard_Screenshot/
python scanfor_red.py --force               # re-process everything
python scanfor_red.py shot.png              # one image -> outputs/json_to_excel/shot/shot.json
python scanfor_red.py shot.png out.json     # explicit output path

# Web app (http://localhost:5000)
python app.py

# Docker (bundles Python + Tesseract + Chromium; nothing else to install)
docker compose up --build                   # web frontend
docker compose run --rm cli                 # batch mode, new screenshots only
docker compose run --rm cli --force         # batch mode, re-do everything

# Module self-tests (no test framework — modules are runnable directly)
python enrich.py                            # prints enrichment for sample alerts

# Publish image to Docker Hub (build + push + run)
./publish.sh
```

There is **no automated test suite, linter, or build step.** Verification is done by running the CLI/app against real screenshots in `Dashboard_Screenshot/`.

### Tesseract requirement (local runs only)

`pytesseract` needs the Tesseract binary installed separately. If it isn't on PATH, set `TESSERACT_CMD` (e.g. `C:\Program Files\Tesseract-OCR\tesseract.exe`). The Docker image already has it on PATH, so no config is needed there.

## Architecture

The pipeline is **screenshot → red-cell detection + OCR → enriched alerts → JSON → Excel**, with a known-error registry layered on top. Understanding it requires reading several modules together:

- **`scanfor_red.py`** — the core. `analyze()` does pure computer vision: `detect_grid()` finds table row/column boundaries from dark grid lines (morphological opening), `red_mask()` color-filters the two red hue ranges (yellow falls outside both, so warnings are excluded on purpose), and each grid cell that is >40% red is OCR'd. **Cells are scanned individually rather than blob-detected** so that vertically stacked red cells stay separate. `process_image()` is the reusable entry point everything else calls — it runs `analyze()`, applies the ticket registry, writes JSON, and auto-generates the Excel report. The `column` field is the hour (cell column index minus 1, since column 0 is the log path).

- **`enrich.py`** — `enrich_with_service_info()` derives a `service_name` from the log path (the directory immediately containing the log file) and looks it up in `service_lookup.json` to attach `error_meaning`. It **always** adds both keys (a "not found" payload when unmatched) so downstream code never guards for absence. Also defines `ERROR_DESCRIPTIONS`, the hour-column → human-readable error mapping shared by the web Error column and Excel.

- **`ticket_registry.py`** — persists manual triage (`Ticket Created?`, `Ticket ID`, `Notes`) for recurring errors in `ticket_registry.json` so they auto-fill on future reports. Matching is most-specific-first: `(system, service, column)` → `(system, service, *)` → `(*, service, column)` → `(*, service, *)`. `upsert()` writes atomically under a lock and **removes** an entry when all three fields are cleared. The web "Save tickets" action feeds this, so triaging one report teaches all future ones.

- **`generate_excel.py`** — `generate_excel(json_path, output_dir)` builds the styled multi-sheet `.xlsx` from an alerts JSON. Called automatically by `process_image()` and re-called by the web app when tickets are saved.

- **`app.py`** — Flask frontend. **It is a flat `app.py` at the repo root, not a package** — `gunicorn app:app` and all imports depend on this. Do **not** create an `app/` directory; it would shadow `app.py` and break the container. Routes reuse `scanfor_red` rather than duplicating logic. The durable source of truth is each screenshot's JSON under `outputs/json_to_excel/<stem>/<stem>.json`; saving tickets rewrites that JSON and regenerates the Excel. `secure_filename` + `_safe_stem()` guard all stem-keyed routes.

- **`services/screenshot_capture.py`** — backs the "Capture Dashboard & Scan" button (`POST /api/capture-and-scan`). `capture_dashboard_screenshot()` screenshots the configured dashboard into `Dashboard_Screenshot/` (timestamped `dashboard_YYYY-MM-DD_HH-MM-SS.png`), then the route feeds it through `sr.process_image`. Playwright is imported **lazily inside functions** so the rest of the app runs without it installed. Two modes via `SCREENSHOT_MODE`: `playwright` (launch Chromium) or `cdp` (drive an already-running, logged-in Chrome over DevTools — used for the VPN-restricted dashboard). Raises `ScreenshotError` with user-safe messages.

## Conventions & constraints

- **Reuse `sr.process_image` / `sr.output_path_for`** for any new scanning entry point — never re-implement the analyze→JSON→Excel flow.
- **Output layout is fixed:** screenshots live in `Dashboard_Screenshot/`; all generated products go to `outputs/json_to_excel/<stem>/<stem>.{json,xlsx}` (one folder per screenshot, keeping generated files out of the screenshot folder). Both are git-ignored and mounted as Docker volumes so writes appear on the host.
- **OCR is intentionally tolerant:** `ocr()` tries multiple page-segmentation modes and both text polarities (dark-on-light log paths vs white-on-red counts). Failed reads become `None`/`""`, never exceptions — one bad cell or image must not sink a batch.
- **Cross-platform requirements split:** `requirements.txt` is platform-neutral; Linux-only deps (`gunicorn`) and the Playwright browser install live in the `Dockerfile`. `opencv-python-headless` (not `opencv-python`) is required — the app never opens GUI windows.
- **Screenshot-capture privacy:** never log the dashboard URL, page HTML, cookies, or tokens. Dashboard config comes from env vars (`DASHBOARD_URL`, `SCREENSHOT_MODE`, `CHROME_CDP_URL`, etc. — see `.env.example`); the real `.env` is git- and docker-ignored.
- **Config tables are data, not code:** `service_lookup.json` (service enrichment) and `ticket_registry.json` (known tickets) are the only JSONs kept in the image; in Docker `ticket_registry.json` is volume-mounted so triage persists.
