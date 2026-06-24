"""
services/screenshot_capture.py — capture a full-page dashboard screenshot
=========================================================================
Grabs a full-page PNG of the configured dashboard so it can be fed straight
into the existing red-box scanner. Everything is driven by environment
variables (see .env.example); no URLs or credentials live in code.

Two capture modes are supported via SCREENSHOT_MODE:

    playwright  Launch a fresh Chromium with Playwright and navigate to
                DASHBOARD_URL. Best when the app itself can reach the
                dashboard (no auth, or auth supplied another way).

    cdp         Connect to an *already running* Chrome over the DevTools
                protocol (CHROME_CDP_URL). You log into the dashboard once
                in that Chrome window — including over a VPN the container
                can't see — and we screenshot the tab. Nothing about the
                session (cookies, tokens, HTML) is read or logged.

Public API:
    capture_dashboard_screenshot() -> dict   # {"screenshot_path": "...", "mode": "..."}

Raises ScreenshotError with a human-readable message on any failure
(missing URL, browser won't launch, CDP unreachable, screenshot failed).

Privacy: this module never prints or logs the dashboard URL, page HTML,
cookies, or tokens. Only the local output filename is surfaced.
"""

import os
from datetime import datetime
from urllib.parse import urlsplit


DEFAULT_OUTPUT_DIR = "/app/Dashboard_Screenshot"
DEFAULT_CDP_URL = "http://host.docker.internal:9222"
DEFAULT_WAIT_MS = 5000


class ScreenshotError(Exception):
    """Raised when a dashboard screenshot cannot be captured.

    The message is safe to show the user — it never contains the dashboard
    URL, page content, or any session data.
    """


# ── Small env helpers ─────────────────────────────────────────────────────────
def _env_int(name, default):
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_bool(name, default):
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _timestamped_path(output_dir):
    """Return output_dir/dashboard_YYYY-MM-DD_HH-MM-SS.png (dir created)."""
    os.makedirs(output_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    return os.path.join(output_dir, f"dashboard_{stamp}.png")


def _same_page(page_url, dashboard_url):
    """True when an open tab is showing (essentially) the dashboard.

    Matches on scheme+host+path and ignores the query string, since the
    dashboard URL carries a per-session token in its query that won't match
    exactly. Compared locally; neither value is logged.
    """
    a, b = urlsplit(page_url), urlsplit(dashboard_url)
    return (a.scheme, a.netloc, a.path) == (b.scheme, b.netloc, b.path)


# ── Capture modes ─────────────────────────────────────────────────────────────
def _capture_playwright(url, out_path, headless, wait_ms):
    """Launch Chromium with Playwright and screenshot the dashboard."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise ScreenshotError(
            "Playwright is not installed. Add it (pip install playwright) and "
            "run 'playwright install chromium'."
        ) from exc

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=headless)
        except Exception as exc:                    # noqa: BLE001 - report cause, not trace
            raise ScreenshotError(
                "Could not launch Chromium. Run 'playwright install chromium' "
                "(and its OS dependencies) first."
            ) from exc

        try:
            # ignore_https_errors covers self-signed / non-standard-port certs.
            context = browser.new_context(ignore_https_errors=True)
            page = context.new_page()
            # 'load' rather than 'networkidle': dashboards that poll never go
            # idle. The explicit wait below gives late content time to render.
            page.goto(url, wait_until="load", timeout=60_000)
            if wait_ms > 0:
                page.wait_for_timeout(wait_ms)
            page.screenshot(path=out_path, full_page=True)
        except Exception as exc:                    # noqa: BLE001
            raise ScreenshotError(
                "Failed to load or screenshot the dashboard. Check the URL is "
                "reachable from where the app runs (VPN, network)."
            ) from exc
        finally:
            browser.close()

    return out_path


def _capture_cdp(url, out_path, cdp_url, wait_ms):
    """Connect to an existing Chrome over CDP and screenshot the dashboard tab."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise ScreenshotError(
            "Playwright is not installed. Add it (pip install playwright) — "
            "the browser itself is your running Chrome, no 'playwright install' "
            "needed for CDP mode."
        ) from exc

    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(cdp_url)
        except Exception as exc:                    # noqa: BLE001
            raise ScreenshotError(
                "Could not connect to Chrome over CDP. Start Chrome with "
                "--remote-debugging-port=9222 and confirm CHROME_CDP_URL is "
                "reachable (host.docker.internal from Docker)."
            ) from exc

        try:
            # Prefer a tab already showing the dashboard (it's logged in);
            # otherwise open a new tab in the existing session and navigate.
            page = None
            for context in browser.contexts:
                for open_page in context.pages:
                    if _same_page(open_page.url, url):
                        page = open_page
                        break
                if page:
                    break

            if page is None:
                context = browser.contexts[0] if browser.contexts else browser.new_context()
                page = context.new_page()
                page.goto(url, wait_until="load", timeout=60_000)

            if wait_ms > 0:
                page.wait_for_timeout(wait_ms)
            page.screenshot(path=out_path, full_page=True)
        except ScreenshotError:
            raise
        except Exception as exc:                    # noqa: BLE001
            raise ScreenshotError(
                "Connected to Chrome but could not capture the dashboard tab. "
                "Make sure the dashboard is open and loaded in that Chrome window."
            ) from exc
        finally:
            # Don't close: it's the user's Chrome. Just drop the connection.
            browser.close()

    return out_path


# ── Public entry point ────────────────────────────────────────────────────────
def capture_dashboard_screenshot():
    """Capture a full-page screenshot of the configured dashboard.

    Returns a dict: {"screenshot_path": str, "mode": str}.
    Raises ScreenshotError (with a user-safe message) on any failure.
    """
    url = os.environ.get("DASHBOARD_URL", "").strip()
    if not url:
        raise ScreenshotError(
            "DASHBOARD_URL is not set. Add it to your environment (.env) before "
            "capturing a screenshot."
        )

    output_dir = os.environ.get("SCREENSHOT_OUTPUT_DIR", "").strip() or DEFAULT_OUTPUT_DIR
    mode = (os.environ.get("SCREENSHOT_MODE", "playwright").strip().lower() or "playwright")
    wait_ms = _env_int("SCREENSHOT_WAIT_MS", DEFAULT_WAIT_MS)

    out_path = _timestamped_path(output_dir)

    if mode == "cdp":
        cdp_url = os.environ.get("CHROME_CDP_URL", "").strip() or DEFAULT_CDP_URL
        _capture_cdp(url, out_path, cdp_url, wait_ms)
    elif mode == "playwright":
        headless = _env_bool("PLAYWRIGHT_HEADLESS", False)
        _capture_playwright(url, out_path, headless, wait_ms)
    else:
        raise ScreenshotError(
            f"Unknown SCREENSHOT_MODE '{mode}'. Use 'playwright' or 'cdp'."
        )

    if not os.path.isfile(out_path) or os.path.getsize(out_path) == 0:
        raise ScreenshotError("Screenshot was not written. Capture failed.")

    return {"screenshot_path": out_path, "mode": mode}
