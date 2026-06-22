"""
scanfor_red.py
==============

Analyze a screenshot of a "VM log alert matrix" and report every RED (critical)
alert cell. For each red cell the script outputs:

    - log_file : the log path at the far left of the cell's row
    - system   : the bold VM/system name heading that section of the table
    - column   : which hour column the cell is in (the header label, 0-13)
    - count    : the number printed inside the red cell

Yellow "warning" cells are intentionally ignored -- only red/critical cells
are reported.

Output is written as JSON under outputs/json_to_excel/<name>/<name>.json,
alongside the Excel report generated from it.

Requirements
------------
    pip install opencv-python numpy pytesseract
    Tesseract OCR engine must also be installed (the binary), e.g. on Windows:
        https://github.com/UB-Mannheim/tesseract/wiki
    If tesseract.exe is not on your PATH, set TESSERACT_CMD below (or the
    TESSERACT_CMD environment variable) to its full path.

Usage
-----
    python scanfor_red.py                      # uses image.png -> results.json
    python scanfor_red.py shot.png             # custom image
    python scanfor_red.py shot.png out.json    # custom image + output
"""

import os
import sys
import json
import argparse

import cv2
import numpy as np
import pytesseract

# ── Enrichment layer ──────────────────────────────────────────
from enrich import load_service_lookup, enrich_with_service_info
from generate_excel import generate_excel

# Load once at startup — not on every image
SERVICE_LOOKUP = load_service_lookup()


# --------------------------------------------------------------------------
# Tesseract location
# --------------------------------------------------------------------------
# If tesseract.exe is not on your PATH, point this at the executable, e.g.:
#   TESSERACT_CMD = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
TESSERACT_CMD = os.environ.get("TESSERACT_CMD", "")
if TESSERACT_CMD:
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD


# --------------------------------------------------------------------------
# Red (critical) cell detection
# --------------------------------------------------------------------------
def red_mask(image):
    """Return a binary mask of red/critical pixels.

    Red lives at both ends of the hue wheel, so we OR two hue ranges.
    The yellow warning colour (~hue 20) falls outside both ranges and is
    therefore ignored. We deliberately do NOT merge blobs here: red cells
    that are stacked vertically must stay separate so the grid can split
    them into individual cells.
    """
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)

    lower_red_1 = np.array([0, 60, 50])
    upper_red_1 = np.array([10, 255, 255])
    lower_red_2 = np.array([165, 60, 50])
    upper_red_2 = np.array([180, 255, 255])

    return cv2.inRange(hsv, lower_red_1, upper_red_1) | cv2.inRange(
        hsv, lower_red_2, upper_red_2
    )


# --------------------------------------------------------------------------
# Table grid detection (row + column boundaries)
# --------------------------------------------------------------------------
def _cluster(positions, gap=4):
    """Collapse runs of adjacent line pixels into single boundary positions."""
    if len(positions) == 0:
        return []
    positions = sorted(int(p) for p in positions)
    clusters = [[positions[0]]]
    for p in positions[1:]:
        if p - clusters[-1][-1] <= gap:
            clusters[-1].append(p)
        else:
            clusters.append([p])
    return [int(round(sum(c) / len(c))) for c in clusters]


def detect_grid(image):
    """Detect the table's column (x) and row (y) boundaries.

    The grid lines are dark and consistent across every section, so we
    accumulate vertical / horizontal line pixels over the whole image and
    keep the strong, repeated positions.
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]

    # Horizontal lines: erode with a wide flat kernel, then sum each row.
    # Real grid lines span ~90% of the width; stray edges from coloured cells
    # only reach ~35%, so a 0.5 threshold keeps the real lines and drops them.
    h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (15, 1))
    horizontal = cv2.morphologyEx(bw, cv2.MORPH_OPEN, h_kernel)
    row_strength = horizontal.sum(axis=1)
    ys = _cluster(np.where(row_strength > 0.50 * row_strength.max())[0])

    # Vertical lines: require a continuous run most of a row tall. Real column
    # lines span the full row height; the left edges of the log-path text only
    # form short strokes, so scaling the kernel to the row height rejects them
    # (otherwise stacked text would be mistaken for an extra column boundary).
    if len(ys) >= 2:
        row_height = int(np.median(np.diff(ys)))
    else:
        row_height = 20
    v_height = max(15, int(row_height * 0.8))
    v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, v_height))
    vertical = cv2.morphologyEx(bw, cv2.MORPH_OPEN, v_kernel)
    col_strength = vertical.sum(axis=0)
    xs = _cluster(np.where(col_strength > 0.30 * col_strength.max())[0])

    return xs, ys


def index_of(boundaries, value):
    """Return the cell index whose [boundary, next_boundary) contains value."""
    for i in range(len(boundaries) - 1):
        if boundaries[i] <= value < boundaries[i + 1]:
            return i
    return None


# --------------------------------------------------------------------------
# OCR helpers
# --------------------------------------------------------------------------
def _crop(image, xs, ys, col, row, pad=3):
    """Crop the cell at (col, row), shrunk slightly to avoid the borders."""
    x0, x1 = xs[col] + pad, xs[col + 1] - pad
    y0, y1 = ys[row] + pad, ys[row + 1] - pad
    if x1 <= x0 or y1 <= y0:
        return None
    return image[y0:y1, x0:x1]


def ocr(cell, digits_only=False, psm=7):
    """Read text from a cell image, trying both text polarities.

    Cells come in two flavours: dark text on a light background (log paths,
    plain counts) and white text on a red background (the count inside a
    critical cell). We upscale, binarise, add a quiet margin (Tesseract reads
    poorly without one), and try both normal and inverted so either flavour
    reads cleanly.
    """
    if cell is None or cell.size == 0:
        return ""

    gray = cv2.cvtColor(cell, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
    base = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]

    # Counts are short and sit alone in a cell; a single thin digit (e.g. "1")
    # reads poorly in line mode, so try several page-segmentation modes and
    # take the first that yields a digit. 10=single char, 7=line, 8=word.
    psms = [10, 7, 8] if digits_only else [psm]

    for p in psms:
        config = f"--psm {p}"
        if digits_only:
            config += " -c tessedit_char_whitelist=0123456789"
        for img in (base, cv2.bitwise_not(base)):
            # Tesseract wants dark text on a light background with a margin.
            framed = cv2.copyMakeBorder(img, 20, 20, 20, 20,
                                        cv2.BORDER_CONSTANT, value=255)
            text = pytesseract.image_to_string(framed, config=config).strip()
            if digits_only:
                text = "".join(ch for ch in text if ch.isdigit())
            if text:
                return text
    return ""


# --------------------------------------------------------------------------
# Main analysis
# --------------------------------------------------------------------------
def analyze(image_path):
    image = cv2.imread(image_path)
    if image is None:
        raise FileNotFoundError(f"Could not read image: {image_path}")

    xs, ys = detect_grid(image)
    if len(xs) < 2 or len(ys) < 2:
        raise RuntimeError("Could not detect the table grid in the image.")

    # Classify every row once: read its left-most cell (column 0).
    #   - text starting with "/"  -> a log row, the text is the log path
    #   - other non-empty text    -> a section header, the text is the system
    #   - empty                   -> a blank spacer row
    row_label = []   # (kind, text) per row band; kind in {"log","system",""}
    for r in range(len(ys) - 1):
        text = ocr(_crop(image, xs, ys, 0, r))
        if text.startswith("/"):
            row_label.append(("log", text))
        elif any(ch.isalpha() for ch in text):
            row_label.append(("system", text))
        else:
            row_label.append(("", text))

    # Walk the grid cell by cell. A cell is a red/critical alert when a large
    # fraction of its pixels are red. Scanning per grid cell (rather than
    # blob-finding) keeps vertically stacked red cells separate.
    mask = red_mask(image)

    alerts = []
    for row in range(len(ys) - 1):
        # Red alerts only ever appear in a log row.
        if row_label[row][0] != "log":
            continue

        # The system is the nearest "system" header at or above this row.
        system = ""
        for r in range(row, -1, -1):
            if row_label[r][0] == "system":
                system = row_label[r][1]
                break

        # Skip the left-most column (the log path); check the hour columns.
        for col in range(1, len(xs) - 1):
            x0, x1 = xs[col], xs[col + 1]
            y0, y1 = ys[row], ys[row + 1]
            cell_mask = mask[y0:y1, x0:x1]
            if cell_mask.size == 0:
                continue
            if (cell_mask > 0).mean() < 0.40:   # not mostly red -> skip
                continue

            # Column layout is [log path, hour 0, hour 1, ... hour 13], so the
            # hour label for a cell is its column index minus one.
            hour = col - 1
            count = ocr(_crop(image, xs, ys, col, row), digits_only=True)

            alert = {
                "log_file": row_label[row][1],
                "system": system,
                "column": hour,
                "count": int(count) if count.isdigit() else None,
            }
            alert = enrich_with_service_info(alert, SERVICE_LOOKUP)
            alerts.append(alert)

    # Stable, readable ordering: top-to-bottom, then left-to-right.
    alerts.sort(key=lambda a: (a["system"], a["log_file"], a["column"]))
    return alerts


IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp"}
DEFAULT_FOLDER = "Dashboard_Screenshot"

# JSON and Excel are written side by side under this folder, one subfolder per
# screenshot — keeping generated output out of the screenshot folder.
OUTPUT_ROOT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "outputs", "json_to_excel"
)


def output_path_for(image_path):
    """Return where an image's JSON is written: outputs/json_to_excel/<stem>/<stem>.json.

    The Excel report lands in the same folder, so both products of one
    screenshot stay together and never clutter the screenshot folder.
    """
    stem = os.path.splitext(os.path.basename(image_path))[0]
    return os.path.join(OUTPUT_ROOT, stem, stem + ".json")


def process_image(image_path, output_path):
    """Analyze one screenshot, write its JSON, and auto-generate the Excel report."""
    alerts = analyze(str(image_path))

    # JSON and Excel share this folder; create it before writing the JSON.
    out_dir = os.path.dirname(os.path.abspath(output_path))
    os.makedirs(out_dir, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(alerts, f, indent=2)

    print(f"\n{os.path.basename(image_path)}: {len(alerts)} red alert(s) -> {os.path.basename(output_path)}")
    for a in alerts:
        print(f"    [{a['system']}] {a['log_file']} "
              f"col={a['column']} count={a['count']}")

    # ── Auto-generate Excel report (same folder as the JSON) ──
    try:
        xlsx_path = generate_excel(output_path, out_dir)
        print(f"    Excel report -> {os.path.relpath(xlsx_path)}")
    except Exception as exc:
        print(f"    [WARN] Excel generation failed: {exc}")

    return alerts


def process_folder(folder, force=False):
    """Process every screenshot in a folder, writing each <name>.json under
    outputs/json_to_excel/<name>/ (next to its Excel report).

    By default a screenshot is skipped when its JSON already exists and is
    newer than the image, so re-running after adding one new screenshot only
    processes that new one. Use force=True to re-process everything.
    """
    images = sorted(
        os.path.join(folder, f) for f in os.listdir(folder)
        if os.path.splitext(f)[1].lower() in IMAGE_EXTS
    )
    if not images:
        print(f"No screenshots found in {folder!r}.")
        return

    processed = skipped = 0
    for image_path in images:
        out_path = output_path_for(image_path)
        if (not force and os.path.exists(out_path)
                and os.path.getmtime(out_path) >= os.path.getmtime(image_path)):
            print(f"{os.path.basename(image_path)}: up to date, skipping "
                  f"(use --force to redo)")
            skipped += 1
            continue
        try:
            process_image(image_path, out_path)
            processed += 1
        except Exception as exc:                       # keep going on a bad file
            print(f"{os.path.basename(image_path)}: ERROR - {exc}")

    print(f"\nDone. {processed} processed, {skipped} skipped, "
          f"{len(images)} total.")


def main():
    parser = argparse.ArgumentParser(
        description="Find red/critical alerts in VM log alert screenshots. "
                    "With no arguments, processes every screenshot in the "
                    f"{DEFAULT_FOLDER!r} folder and writes one JSON per image."
    )
    parser.add_argument(
        "path", nargs="?", default=DEFAULT_FOLDER,
        help=f"A screenshot file OR a folder of screenshots "
             f"(default: the {DEFAULT_FOLDER!r} folder)")
    parser.add_argument(
        "output", nargs="?", default=None,
        help="Output JSON path (only when 'path' is a single image; "
             "defaults to outputs/json_to_excel/<name>/<name>.json)")
    parser.add_argument(
        "--force", action="store_true",
        help="Re-process screenshots even if their JSON is already up to date")
    args = parser.parse_args()

    if os.path.isdir(args.path):
        process_folder(args.path, force=args.force)
    else:
        out_path = args.output or output_path_for(args.path)
        process_image(args.path, out_path)


if __name__ == "__main__":
    sys.exit(main())
