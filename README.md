# Scanfor_Red — Red Alert Screenshot Scanner

Analyze a screenshot of a **VM log alert matrix** and report every **red /
critical** cell. Yellow "warning" cells are ignored on purpose.

For each red cell the tool reports:

| Field      | Meaning                                                      |
|------------|-------------------------------------------------------------|
| `log_file` | The log path at the far left of that row                    |
| `system`   | The bold VM/system name heading that section of the table   |
| `column`   | Which hour column the cell is in (`0`–`13`)                  |
| `count`    | The number printed inside the red cell                      |

Results are written to a JSON file.

There are **two ways** to run it:

- **[Docker](#run-with-docker-easiest)** — nothing to install but Docker
  itself; Python and the Tesseract OCR engine are baked into the image.
- **[Local Python](#1-one-time-setup)** — run it directly with your own Python
  (sections 1–3 below).

---

## Run with Docker (easiest)

The container bundles Python, OpenCV, and the Tesseract OCR engine, so you do
**not** need to install Python, `pytesseract`, or Tesseract on your machine —
only Docker.

### One-time: install Docker Desktop

```powershell
winget install --id Docker.DockerDesktop -e
```

Then launch **Docker Desktop** once and let it finish starting (the whale icon
in the system tray stops animating). A sign-in is **not** required for this.

### Start the web app

From this project folder:

```powershell
docker compose up --build
```

The first run builds the image (downloads the base image + Tesseract); later
runs start instantly. When it says it's listening, open:

```
http://localhost:5000
```

Upload one or more screenshots in the browser. Each is scanned, its JSON and
Excel report are written to `outputs\json_to_excel\<name>\`, and a summary of
the results is shown on the page. Press **Ctrl+C** in the terminal to stop the
server.

> The `Dashboard_Screenshot` and `outputs\json_to_excel` folders are mounted
> into the container, so everything it writes appears right back on your machine.

### Optional: batch mode without the browser

To process the whole `Dashboard_Screenshot\` folder at once (no upload UI):

```powershell
docker compose run --rm cli            # only new screenshots
docker compose run --rm cli --force    # re-do everything
```

---

## 1. One-time setup

You only have to do this **once** on a machine.

### a. Install Python packages

Open **PowerShell** in this folder and run:

```powershell
python -m pip install opencv-python numpy pytesseract
```

### b. Install the Tesseract OCR engine

`pytesseract` is just a wrapper — it needs the actual Tesseract program
installed separately.

```powershell
winget install --id UB-Mannheim.TesseractOCR -e
```

This installs it to `C:\Program Files\Tesseract-OCR\tesseract.exe`.

> Already installed on this machine (Tesseract v5.4.0). You can skip this step
> here, but you'll need it on any new computer.

### c. Make Tesseract findable

The script needs to know where `tesseract.exe` is. Pick **one** option:

- **Option 1 — easiest (per session):** set an environment variable before
  running. This lasts only for the current PowerShell window:

  ```powershell
  $env:TESSERACT_CMD = "C:\Program Files\Tesseract-OCR\tesseract.exe"
  ```

- **Option 2 — permanent:** add Tesseract to your PATH once, then it just
  works in every new terminal:

  ```powershell
  [Environment]::SetEnvironmentVariable("Path", $env:Path + ";C:\Program Files\Tesseract-OCR", "User")
  ```

  Close and reopen PowerShell after running this.

---

## 2. Running it on an image

### A specific image

```powershell
python scanfor_red.py myscreenshot.png
```

This writes `myscreenshot.json` next to the image (same name, `.json`
extension). You can also give an explicit output name:

```powershell
python scanfor_red.py myscreenshot.png myscreenshot_results.json
```

You'll see a summary in the terminal, for example:

```
myscreenshot.png: 1 red alert(s) -> myscreenshot.json
    [labcore_cars-ccgw-eastus2-prod-aeris-vm-01] /appl/.../aerislistener-main.20260603 col=3 count=2
```

…and the JSON file will contain:

```json
[
  {
    "log_file": "/appl/labcore_cars/log/cars/aerislistener/aerislistener-main.20260603",
    "system": "labcore_cars-ccgw-eastus2-prod-aeris-vm-01",
    "column": 3,
    "count": 2
  }
]
```

If there are no red cells, you'll see `0 red alert(s)` and the JSON file will
be an empty `[]` — that's normal, it means nothing is critical.

---

## 3. The daily workflow (the `Dashboard_Screenshot` folder)

This is the main way to use the tool day to day.

1. Save each day's screenshot into the **`Dashboard_Screenshot`** folder, named
   by its date — e.g. `6_1.png`, `6_2.png`, `6_3.png`.
2. Run the script with **no arguments**:

   ```powershell
   python scanfor_red.py
   ```

3. It scans every image in that folder and writes a matching JSON next to each
   one — `6_1.png` → `6_1.json`, `6_2.png` → `6_2.json`, and so on. One JSON
   file per day.

Example output:

```
6_1.png: 44 red alert(s) -> 6_1.json
6_2.png: 31 red alert(s) -> 6_2.json
6_3.png: 27 red alert(s) -> 6_3.json

Done. 3 processed, 0 skipped, 3 total.
```

### Only new screenshots are processed

When you add **one** new screenshot and re-run, the script skips images that
already have an up-to-date JSON, so it only processes the new one:

```
6_1.png: up to date, skipping (use --force to redo)
6_2.png: up to date, skipping (use --force to redo)
6_4.png: 19 red alert(s) -> 6_4.json

Done. 1 processed, 2 skipped, 3 total.
```

### Re-doing everything

If you replace a screenshot or change the script and want to re-run all of
them, add `--force`:

```powershell
python scanfor_red.py --force
```

### A different folder

Point it at any folder instead of the default:

```powershell
python scanfor_red.py "C:\path\to\some_other_folder"
```

> Accepted image types: `.png`, `.jpg`, `.jpeg`, `.bmp`.

---

## 3b. Web frontend (upload in the browser)

Prefer uploading instead of dropping files in a folder? There's a small web app.

```powershell
python -m pip install Flask
python app.py
```

Then open **http://localhost:5000**, drag in one or more screenshots, and click
**Scan & Generate Report**. Each screenshot is run through the same pipeline; its
JSON and Excel report are saved under `outputs\json_to_excel\<name>\`, and a
summary (KPIs, top systems, per-screenshot alert tables, download links) is shown
on the page.

> The same frontend is what the Docker container serves by default — see
> [Run with Docker](#run-with-docker-easiest). If Tesseract isn't on your PATH,
> set `TESSERACT_CMD` first (step **1c**).

---

## 4. Quick checklist if something goes wrong

### Local Python

| Symptom                                            | Fix                                                                 |
|----------------------------------------------------|---------------------------------------------------------------------|
| `No module named 'pytesseract'`                    | Run step **1a**.                                                    |
| `tesseract is not installed or it's not in your PATH` | Run step **1b**, then **1c**.                                   |
| `Could not read image`                             | Check the file name/path is correct and the file exists.            |
| `Could not detect the table grid`                  | The screenshot must show the full table with its grid lines.        |
| Counts or text look wrong                          | Use a higher-resolution / non-blurry screenshot (full size, not shrunk). |

### Docker

| Symptom                                            | Fix                                                                 |
|----------------------------------------------------|---------------------------------------------------------------------|
| `docker : The term 'docker' is not recognized`     | Install Docker Desktop (see the Docker section) and reopen PowerShell. |
| `error during connect` / `pipe ... docker_engine`  | Docker Desktop isn't running — launch it and wait for it to finish starting. |
| `http://localhost:5000` won't load                 | Give it a few seconds after `docker compose up`, and confirm the terminal shows it listening. |
| `port is already allocated` (5000 in use)          | Edit `docker-compose.yml` and change the mapping to e.g. `"5050:5000"`, then use `http://localhost:5050`. |
| Files don't appear on my machine                   | Run from this project folder so the `./Dashboard_Screenshot` and `./outputs` mounts resolve correctly. |
| `ImportError: libGL.so.1`                          | Add `libgl1` to the `apt-get` list in the `Dockerfile`, then `docker compose build`. |

---

## 5. How it works (short version)

1. **Detect the table grid** — locates the row and column lines so the image
   can be split into individual cells.
2. **Scan every cell for red** — color-filters for the critical red shade
   (`#c93e47`) and flags any cell that is mostly red; the yellow warning color
   is deliberately excluded. Scanning per cell keeps stacked red cells separate
   (so three reds in a column become three results, not one).
3. **Read the text (OCR)** — uses Tesseract to read the log path, the system
   header, and the number inside each red cell.
4. **Write JSON** — outputs one entry per red alert.

The column is derived from the cell's position (column index minus one = the
hour), and the count is read from inside that specific cell.
