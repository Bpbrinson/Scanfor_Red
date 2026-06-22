# Scanfor Red

Detect **red / critical alerts** in VM log-dashboard screenshots and turn them
into a structured JSON + a formatted Excel report — through a simple web UI.

Upload a screenshot of the VM log alert matrix; the app OCR-reads each red cell
and reports the **log file**, **system**, **hour column**, and **count**, then
enriches every alert with its service, error description, remediation hint, and
escalation team. Yellow "warning" cells are ignored — only red/critical ones.

---

## Quick start

```bash
docker run --rm -p 5000:5000 YOURNAME/scanfor-red:latest
```

Then open **http://localhost:5000**, drag in a screenshot, and click
**Scan & Generate Report**. Download the Excel/JSON from the results page.

### Keep results between restarts

```bash
docker run --rm -p 5000:5000 \
  -v scanfor_screens:/app/Dashboard_Screenshot \
  -v scanfor_outputs:/app/outputs/json_to_excel \
  YOURNAME/scanfor-red:latest
```

---

## What's inside

- Python + OpenCV for grid/colour detection
- **Tesseract OCR** bundled in the image — nothing to install on the host
- Flask web frontend (upload, summary, ticket tracking, delete)
- `openpyxl` for the styled, multi-sheet Excel report

## Ports & volumes

| Setting | Value |
|---------|-------|
| Port | `5000` (the web UI) |
| Volume | `/app/Dashboard_Screenshot` — uploaded screenshots + JSON |
| Volume | `/app/outputs/json_to_excel` — generated Excel reports |

## Batch / CLI mode (no browser)

Process a whole folder of screenshots instead of the web UI:

```bash
docker run --rm \
  -v "$PWD/Dashboard_Screenshot:/app/Dashboard_Screenshot" \
  -v "$PWD/outputs/json_to_excel:/app/outputs/json_to_excel" \
  YOURNAME/scanfor-red:latest python scanfor_red.py
```

## Tags

- `latest` — current build

> A plain build on Apple Silicon is `linux/arm64`. For Intel/amd64 too, build
> multi-arch: `docker buildx build --platform linux/amd64,linux/arm64 --push`.

Source: https://github.com/YOURNAME/Scanfor_Red
