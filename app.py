"""
app.py — Web frontend for Scanfor_Red
=====================================
Upload one or more dashboard screenshots in the browser. Each one is run
through the existing pipeline (analyze -> JSON -> enriched Excel report),
the products are stored under outputs/json_to_excel/<stem>/, and a summary
of the results is shown back on the page.

Run:
    python app.py
    # then open http://localhost:5000

If Tesseract is not on PATH, set TESSERACT_CMD first (see README), e.g.:
    $env:TESSERACT_CMD = "C:\\Program Files\\Tesseract-OCR\\tesseract.exe"
"""

import os
import json
import shutil
from datetime import datetime

from flask import (
    Flask, request, render_template, redirect, url_for, flash, send_file, abort,
    jsonify,
)
from werkzeug.utils import secure_filename

import scanfor_red as sr            # reuse analyze / process_image / paths / enrichment
from generate_excel import generate_excel as build_excel
from enrich import ERROR_DESCRIPTIONS   # column-number -> human-readable error
from ticket_registry import (
    upsert as upsert_registry,
    load_registry,
    delete_entry as delete_reg_entry,
)
from services.screenshot_capture import (
    capture_dashboard_screenshot, ScreenshotError,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, sr.DEFAULT_FOLDER)   # Dashboard_Screenshot
OUTPUT_ROOT = sr.OUTPUT_ROOT                             # outputs/json_to_excel

app = Flask(__name__)
app.secret_key = "scanfor-red-frontend"
app.config["MAX_CONTENT_LENGTH"] = 60 * 1024 * 1024      # 60 MB per request


# ── Helpers ──────────────────────────────────────────────────────────────────
def build_summary(alerts):
    """Compute the same KPIs shown on the Excel Summary sheet."""
    systems = sorted({a["system"] for a in alerts})
    services = sorted({a.get("service_name", "") for a in alerts if a.get("service_name")})
    logs = sorted({a["log_file"] for a in alerts})
    total = sum((a.get("count") or 0) for a in alerts)
    peak = max((a.get("count") or 0) for a in alerts) if alerts else 0
    by_system = sorted(
        ({"system": s,
          "alerts": sum(1 for a in alerts if a["system"] == s),
          "total": sum((a.get("count") or 0) for a in alerts if a["system"] == s)}
         for s in systems),
        key=lambda x: x["total"], reverse=True,
    )
    return {
        "total_alerts": len(alerts),
        "unique_systems": len(systems),
        "unique_services": len(services),
        "unique_logs": len(logs),
        "total_count": total,
        "peak": peak,
        "top_systems": by_system[:10],
    }


def _safe_stem(stem):
    """Sanitise a URL-supplied stem and confirm its output folder exists."""
    stem = secure_filename(stem)
    folder = os.path.join(OUTPUT_ROOT, stem)
    if not os.path.isdir(folder):
        abort(404)
    return stem, folder


def recent_results(limit=20):
    """List previously processed screenshots (newest first) for the home page."""
    if not os.path.isdir(OUTPUT_ROOT):
        return []
    items = []
    for stem in os.listdir(OUTPUT_ROOT):
        json_path = os.path.join(OUTPUT_ROOT, stem, stem + ".json")
        xlsx_path = os.path.join(OUTPUT_ROOT, stem, stem + ".xlsx")
        if os.path.isfile(json_path):
            items.append({
                "stem": stem,
                "when": datetime.fromtimestamp(os.path.getmtime(json_path)),
                "has_excel": os.path.isfile(xlsx_path),
            })
    items.sort(key=lambda x: x["when"], reverse=True)
    return items[:limit]


# ── Routes ───────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html", recent=recent_results())


@app.route("/result/<stem>")
def view_result(stem):
    """Reopen a previously processed screenshot, with its saved ticket fields
    pre-filled, so the 'saved' indicators reflect what is stored."""
    stem, folder = _safe_stem(stem)
    json_path = os.path.join(folder, stem + ".json")
    if not os.path.isfile(json_path):
        abort(404)
    with open(json_path, encoding="utf-8") as f:
        alerts = json.load(f)
    when = datetime.fromtimestamp(os.path.getmtime(json_path)).strftime("%Y-%m-%d %H:%M")
    result = {"name": stem, "stem": stem, "summary": build_summary(alerts), "alerts": alerts}
    return render_template(
        "results.html",
        results=[result],
        overall=build_summary(alerts),
        processed=1,
        generated=when,
        viewing=True,
        errors=ERROR_DESCRIPTIONS,
    )


@app.route("/upload", methods=["POST"])
def upload():
    files = [f for f in request.files.getlist("screenshots") if f and f.filename]
    if not files:
        flash("Please choose at least one screenshot to upload.")
        return redirect(url_for("index"))

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    results, all_alerts = [], []

    for f in files:
        name = secure_filename(f.filename)
        ext = os.path.splitext(name)[1].lower()
        if ext not in sr.IMAGE_EXTS:
            results.append({"name": name, "error": f"Unsupported file type '{ext}'."})
            continue

        save_path = os.path.join(UPLOAD_DIR, name)
        f.save(save_path)
        stem = os.path.splitext(name)[0]
        output_path = sr.output_path_for(save_path)

        try:
            alerts = sr.process_image(save_path, output_path)
        except Exception as exc:                       # one bad image won't sink the batch
            results.append({"name": name, "error": str(exc)})
            continue

        all_alerts.extend(alerts)
        results.append({
            "name": name,
            "stem": stem,
            "summary": build_summary(alerts),
            "alerts": alerts,
        })

    overall = build_summary(all_alerts) if all_alerts else None
    processed = sum(1 for r in results if "error" not in r)
    return render_template(
        "results.html",
        results=results,
        overall=overall,
        processed=processed,
        generated=datetime.now().strftime("%Y-%m-%d %H:%M"),
        errors=ERROR_DESCRIPTIONS,
    )


@app.route("/api/capture-and-scan", methods=["POST"])
def capture_and_scan():
    """Capture a full-page screenshot of the configured dashboard, then run it
    through the same scanner the upload flow uses.

    Returns JSON. Reuses sr.process_image so the JSON + Excel products land in
    outputs/json_to_excel/<stem>/ exactly like an uploaded screenshot would.
    """
    try:
        capture = capture_dashboard_screenshot()
    except ScreenshotError as exc:
        # ScreenshotError messages are written to be user-safe (no URL/secrets).
        return jsonify({"success": False, "message": str(exc)}), 502
    except Exception:                              # noqa: BLE001 - don't leak internals
        return jsonify({
            "success": False,
            "message": "Unexpected error while capturing the dashboard screenshot.",
        }), 500

    screenshot_path = capture["screenshot_path"]
    stem = os.path.splitext(os.path.basename(screenshot_path))[0]
    output_path = sr.output_path_for(screenshot_path)

    try:
        alerts = sr.process_image(screenshot_path, output_path)
    except Exception as exc:                       # noqa: BLE001 - scanner failure is user-facing
        return jsonify({
            "success": False,
            "message": f"Screenshot captured but scanning failed: {exc}",
            "screenshot_path": screenshot_path,
        }), 500

    xlsx_path = os.path.join(os.path.dirname(output_path), stem + ".xlsx")
    output_file = xlsx_path if os.path.isfile(xlsx_path) else output_path

    return jsonify({
        "success": True,
        "screenshot_path": screenshot_path,
        "stem": stem,
        "red_boxes_found": len(alerts),
        "output_file": output_file,
        "message": "Screenshot captured and scan completed successfully.",
    })


@app.route("/download/<stem>/json")
def download_json(stem):
    stem, folder = _safe_stem(stem)
    return send_file(os.path.join(folder, stem + ".json"), as_attachment=True)


@app.route("/download/<stem>/excel")
def download_excel(stem):
    stem, folder = _safe_stem(stem)
    path = os.path.join(folder, stem + ".xlsx")
    if not os.path.isfile(path):
        abort(404)
    return send_file(path, as_attachment=True)


@app.route("/save-tickets/<stem>", methods=["POST"])
def save_tickets(stem):
    """Persist the manual 'Ticket Created?' entries for one screenshot.

    The values are written into the screenshot's JSON (the durable source of
    truth) as a `ticket_exists` field per alert, then the Excel report is
    regenerated so the Data and Ticket Check sheets reflect them.
    """
    stem, folder = _safe_stem(stem)
    json_path = os.path.join(folder, stem + ".json")
    if not os.path.isfile(json_path):
        abort(404)

    payload = request.get_json(silent=True) or {}
    keyed = {
        (t.get("system", ""), t.get("log_file", ""), t.get("column")): {
            "ticket_exists": (t.get("ticket") or "").strip(),
            "ticket_id": (t.get("ticket_id") or "").strip(),
            "ticket_notes": (t.get("ticket_notes") or "").strip(),
        }
        for t in payload.get("tickets", [])
    }

    with open(json_path, encoding="utf-8") as f:
        alerts = json.load(f)

    updated = 0
    learned = []   # rows to teach the known-error registry
    for a in alerts:
        key = (a.get("system", ""), a.get("log_file", ""), a.get("column"))
        if key in keyed:
            fields = keyed[key]
            if any((a.get(k) or "") != v for k, v in fields.items()):
                updated += 1
            a.update(fields)
            learned.append({
                "system": a.get("system", ""),
                "service": a.get("service_name", ""),
                "column": a.get("column"),
                **fields,
            })

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(alerts, f, indent=2)

    # Remember these tickets so future reports auto-fill the same errors.
    upsert_registry(learned)

    try:
        build_excel(json_path, folder)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500

    return jsonify({"ok": True, "updated": updated})


@app.route("/delete/<stem>", methods=["POST"])
def delete_result(stem):
    """Delete a screenshot and everything generated from it (JSON, Excel,
    previews) — for managing/cleaning up old dates."""
    safe = secure_filename(stem)
    if not safe:
        abort(400)

    removed = []
    # The screenshot itself (any supported extension).
    for ext in sr.IMAGE_EXTS:
        p = os.path.join(UPLOAD_DIR, safe + ext)
        if os.path.isfile(p):
            os.remove(p)
            removed.append(safe + ext)
    # A JSON sitting next to the screenshot (older output layout).
    legacy = os.path.join(UPLOAD_DIR, safe + ".json")
    if os.path.isfile(legacy):
        os.remove(legacy)
        removed.append(safe + ".json")
    # The whole report folder (JSON + Excel + previews), kept inside OUTPUT_ROOT.
    folder = os.path.join(OUTPUT_ROOT, safe)
    if os.path.isdir(folder) and \
            os.path.normpath(folder).startswith(os.path.normpath(OUTPUT_ROOT) + os.sep):
        shutil.rmtree(folder)
        removed.append(safe + "\\ (report folder)")

    if removed:
        flash(f"Deleted {safe} — removed: " + ", ".join(removed))
    else:
        flash(f"Nothing found to delete for {safe}.")
    return redirect(url_for("index"))


# ── Known-alert registry management ─────────────────────────────────────────

def _parse_col(v):
    """Return int for a numeric column value, None for blank / '*' (wildcard)."""
    try:
        return int(v) if v not in (None, "", "*") else None
    except (TypeError, ValueError):
        return None


@app.route("/registry")
def registry():
    entries = load_registry()
    open_count = sum(1 for e in entries if e.get("ticket_exists") == "Yes")
    wildcard_count = sum(
        1 for e in entries
        if (not e.get("system") or e.get("system") == "*") or e.get("column") is None
    )
    return render_template(
        "registry.html",
        entries=entries,
        errors=ERROR_DESCRIPTIONS,
        total=len(entries),
        open_count=open_count,
        wildcard_count=wildcard_count,
    )


@app.route("/api/registry/upsert", methods=["POST"])
def registry_upsert():
    data = request.get_json(silent=True) or {}
    service = (data.get("service") or "").strip()
    if not service:
        return jsonify({"ok": False, "error": "service is required"}), 400
    item = {
        "system": (data.get("system") or "").strip() or "*",
        "service": service,
        "column": _parse_col(data.get("column")),
        "ticket_exists": (data.get("ticket_exists") or "").strip(),
        "ticket_id": (data.get("ticket_id") or "").strip(),
        "ticket_notes": (data.get("ticket_notes") or "").strip(),
    }
    count = upsert_registry([item])
    return jsonify({"ok": True, "total": count})


@app.route("/api/registry/delete", methods=["POST"])
def registry_delete():
    data = request.get_json(silent=True) or {}
    service = (data.get("service") or "").strip()
    if not service:
        return jsonify({"ok": False, "error": "service is required"}), 400
    system = (data.get("system") or "").strip() or "*"
    removed = delete_reg_entry(system, service, _parse_col(data.get("column")))
    return jsonify({"ok": removed})


@app.route("/api/registry/update", methods=["POST"])
def registry_update():
    """Replace one registry entry. old_key identifies the existing row; new_entry is the replacement."""
    data = request.get_json(silent=True) or {}
    old = data.get("old_key", {})
    new = data.get("new_entry", {})
    service = (new.get("service") or "").strip()
    if not service:
        return jsonify({"ok": False, "error": "service is required"}), 400

    old_system = (old.get("system") or "").strip() or "*"
    old_service = (old.get("service") or "").strip()
    old_col = _parse_col(old.get("column"))
    new_system = (new.get("system") or "").strip() or "*"
    new_col = _parse_col(new.get("column"))

    if old_service and (old_system, old_service, old_col) != (new_system, service, new_col):
        delete_reg_entry(old_system, old_service, old_col)

    item = {
        "system": new_system,
        "service": service,
        "column": new_col,
        "ticket_exists": (new.get("ticket_exists") or "").strip(),
        "ticket_id": (new.get("ticket_id") or "").strip(),
        "ticket_notes": (new.get("ticket_notes") or "").strip(),
    }
    count = upsert_registry([item])
    return jsonify({"ok": True, "total": count})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
