"""One real Chromium browser (Playwright) shared by every step: reading Reddit's
public JSON listings, screenshotting threads, and rendering cards.

Only content a logged-out visitor can see is used. Requests are spaced out, a 429
(rate limited) is waited out as Reddit asks, and a 403 stops the run rather than
being worked around.
"""
from __future__ import annotations

import json
import logging
import os
import random
import time

log = logging.getLogger(__name__)


class RedditBlocked(RuntimeError):
    pass


class Browser:
    def __init__(self, width: int = 1080, height: int = 1920, headless: bool = True,
                 delay: float = 3.0):
        self.w, self.h = width, height
        self.headless = headless
        self.delay = delay
        self._last_request = 0.0

    def __enter__(self):
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        # Optional: point at an existing Chrome/Chromium instead of Playwright's download.
        exe = os.environ.get("CHROMIUM_EXECUTABLE") or None
        self._browser = self._pw.chromium.launch(headless=self.headless, executable_path=exe)
        self.context = self._browser.new_context(locale="en-US")
        self.page = self.context.new_page()  # reused for renders
        self.page.set_viewport_size({"width": self.w, "height": self.h})
        return self

    def __exit__(self, *exc):
        self.context.close()
        self._browser.close()
        self._pw.stop()

    def new_context(self, **kwargs):
        return self._browser.new_context(locale="en-US", **kwargs)

    def headed_context(self):
        """A visible (minimised) browser, for sites that refuse headless Chromium.
        Returns (browser, context); close the browser when done."""
        exe = os.environ.get("CHROMIUM_EXECUTABLE") or None
        br = self._pw.chromium.launch(headless=False, executable_path=exe,
                                      args=["--start-minimized", "--window-size=1100,800"])
        return br, br.new_context(locale="en-US", viewport={"width": 1100, "height": 760})

    def polite_wait(self) -> None:
        """Keep at least `delay` seconds (plus a little jitter) between Reddit requests."""
        wait = self._last_request + self.delay + random.uniform(0, 1) - time.time()
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.time()

    def get_json(self, url: str, retries: int = 3):
        for attempt in range(1, retries + 1):
            self.polite_wait()
            resp = self.context.request.get(url, headers={"Accept": "application/json"}, timeout=30000)
            if resp.status == 200:
                try:
                    return resp.json()
                except (json.JSONDecodeError, ValueError):
                    raise RedditBlocked(f"Reddit returned a non-JSON page for {url} (probably a block page)")
            if resp.status == 429 and attempt < retries:
                wait = int(resp.headers.get("retry-after", "60") or 60)
                log.warning("Rate limited by Reddit; waiting %ss as asked", wait)
                time.sleep(min(wait, 300))
                continue
            if resp.status in (401, 403):
                raise RedditBlocked(f"Reddit refused {url} ({resp.status}). Try again later or from another network.")
            raise RuntimeError(f"GET {url} failed with HTTP {resp.status}")
        raise RuntimeError(f"GET {url} kept being rate limited")
