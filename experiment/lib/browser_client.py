# Drives the jiuwenswarm web UI for one benchmark query: send the message, wait
# for the backend log to report the request finished (or the timeout to
# expire), and return the final transcript text plus timing/status.
#
# Completion is detected via the backend log ("run_stream_task finished"),
# not by polling document.body.innerText for a "处理中" marker: direct
# observation showed that marker does not reliably appear in innerText for
# Plan-mode responses (it may only ever render as a button icon state or an
# input placeholder, neither of which innerText captures), which made a
# text-polling approach silently burn the full per-test timeout on every run.

from __future__ import annotations

import time
from dataclasses import dataclass

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError, sync_playwright

from . import log_parser


@dataclass
class QueryResult:
    status: str  # "ok" | "timeout" | "error"
    start_time: float  # time.time() right before the message is sent
    end_time: float  # time.time() when settled (or timeout fired)
    duration_seconds: float
    final_text: str
    error: str | None = None


def run_single_query(
    page: Page,
    query: str,
    *,
    log_path: str,
    log_start_line: int,
    per_test_timeout_seconds: int,
    post_complete_grace_seconds: int,
) -> QueryResult:
    start_time = time.time()
    try:
        input_box = page.locator("textarea, [contenteditable='true']").first
        input_box.click()
        input_box.fill(query)
        page.keyboard.press("Enter")

        remaining = per_test_timeout_seconds - (time.time() - start_time)
        finished = log_parser.wait_for_stream_finished(
            log_path,
            start_line=log_start_line,
            timeout_seconds=max(remaining, 1),
        )
        status = "ok" if finished else "timeout"

        # Grace window after the main response lands: catches a same-turn
        # a2ui_repair/a2ui_retry_plain follow-up call, which logs its own
        # usage a few seconds after the primary "finished" line. Not a
        # guarantee for every case (a slow retry can still land after this
        # window closes) - see README's Timeouts and failure handling.
        time.sleep(post_complete_grace_seconds)
        end_time = time.time()
        final_text = page.evaluate("document.body.innerText")
        return QueryResult(
            status=status,
            start_time=start_time,
            end_time=end_time,
            duration_seconds=end_time - start_time,
            final_text=final_text,
        )
    except PlaywrightTimeoutError as exc:
        end_time = time.time()
        return QueryResult(
            status="error",
            start_time=start_time,
            end_time=end_time,
            duration_seconds=end_time - start_time,
            final_text="",
            error=f"playwright timeout: {exc}",
        )
    except Exception as exc:  # noqa: BLE001 - benchmark harness must not crash on one bad test
        end_time = time.time()
        return QueryResult(
            status="error",
            start_time=start_time,
            end_time=end_time,
            duration_seconds=end_time - start_time,
            final_text="",
            error=str(exc),
        )


def new_page(playwright, base_url: str):
    browser = playwright.chromium.launch(args=["--no-sandbox"])
    page = browser.new_page()
    page.goto(base_url, wait_until="networkidle", timeout=30_000)
    page.wait_for_timeout(1000)
    return browser, page


__all__ = ["QueryResult", "run_single_query", "new_page", "sync_playwright"]
