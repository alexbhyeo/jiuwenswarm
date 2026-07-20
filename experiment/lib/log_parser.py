# Parses agent_server.log for [JiuWenSwarmDeepAdapter] llm_usage chunk lines and
# aggregates token usage by call_purpose (main / a2ui_repair / a2ui_retry_plain)
# within a given wall-clock time window.
#
# Correlation is by time window, not session_id: tests in this benchmark run
# strictly sequentially (one browser page, one query, wait for completion, next
# query), so "every llm_usage line logged between request-sent and
# response-settled" unambiguously belongs to that test case. This avoids needing
# to thread request_id through the a2ui_repair call site, which only has a raw
# prompt string available at the point it invokes the model.

from __future__ import annotations

import ast
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

_LINE_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})\s+\S+\s+\S+:\s+"
    r"\[JiuWenSwarmDeepAdapter\] llm_usage chunk: "
    r"(?:session_id=\S* request_id=\S* )?"
    r"call_purpose=(?P<purpose>\w+)\s+.*?"
    r"payload=(?P<payload>\{.*\})\s*$"
)

_TS_FMT = "%Y-%m-%d %H:%M:%S.%f"


@dataclass
class UsageTotals:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cache_tokens: int = 0
    call_count: int = 0

    def add(self, usage_metadata: dict) -> None:
        self.input_tokens += int(usage_metadata.get("input_tokens", 0) or 0)
        self.output_tokens += int(usage_metadata.get("output_tokens", 0) or 0)
        self.total_tokens += int(usage_metadata.get("total_tokens", 0) or 0)
        self.cache_tokens += int(usage_metadata.get("cache_tokens", 0) or 0)
        self.call_count += 1

    def as_dict(self) -> dict:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "cache_tokens": self.cache_tokens,
            "call_count": self.call_count,
        }


@dataclass
class WindowUsage:
    by_purpose: dict[str, UsageTotals] = field(default_factory=dict)

    def add(self, purpose: str, usage_metadata: dict) -> None:
        self.by_purpose.setdefault(purpose, UsageTotals()).add(usage_metadata)

    @property
    def total_tokens(self) -> int:
        return sum(u.total_tokens for u in self.by_purpose.values())

    @property
    def input_tokens(self) -> int:
        return sum(u.input_tokens for u in self.by_purpose.values())

    @property
    def output_tokens(self) -> int:
        return sum(u.output_tokens for u in self.by_purpose.values())

    def as_dict(self) -> dict:
        return {purpose: totals.as_dict() for purpose, totals in self.by_purpose.items()}


def _parse_ts(text: str) -> datetime:
    return datetime.strptime(text, _TS_FMT)


def tail_line_count(log_path: str | Path) -> int:
    """Return the current line count of the log file, used as a start marker
    so a later parse only scans lines appended after this point."""
    path = Path(log_path)
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8", errors="replace") as f:
        return sum(1 for _ in f)


_STREAM_FINISHED_RE = re.compile(r"\[JiuWenSwarm\] run_stream_task finished: request_id=")


def wait_for_stream_finished(
    log_path: str | Path,
    *,
    start_line: int,
    timeout_seconds: float,
    poll_interval_seconds: float = 1.0,
) -> bool:
    """Poll the log file for a "run_stream_task finished" line appearing after
    `start_line`. Returns True once found, False on timeout.

    This replaces polling the frontend's "处理中" UI text, which turned out to
    not reliably appear in `document.body.innerText` for Plan-mode responses
    (confirmed by direct observation: the DOM text never contained the marker
    even while a real ~28s response was in flight). The backend log line is
    the actual, unambiguous signal the harness needs anyway to close the
    usage-window for parse_usage_window.
    """
    deadline = time.time() + timeout_seconds
    path = Path(log_path)
    while time.time() < deadline:
        if path.exists():
            with path.open("r", encoding="utf-8", errors="replace") as f:
                for idx, line in enumerate(f):
                    if idx < start_line:
                        continue
                    if _STREAM_FINISHED_RE.search(line):
                        return True
        time.sleep(poll_interval_seconds)
    return False


def parse_usage_window(
    log_path: str | Path,
    *,
    start_line: int = 0,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
    end_grace: timedelta = timedelta(seconds=2),
) -> WindowUsage:
    """Aggregate llm_usage lines appearing after `start_line`, optionally
    further filtered to a [window_start, window_end + end_grace] timestamp range.

    `start_line` alone is normally sufficient since the harness reads the file
    sequentially per test case; the timestamp filter is a defensive extra check.
    """
    path = Path(log_path)
    result = WindowUsage()
    if not path.exists():
        return result

    with path.open("r", encoding="utf-8", errors="replace") as f:
        for idx, line in enumerate(f):
            if idx < start_line:
                continue
            m = _LINE_RE.match(line)
            if not m:
                continue
            if window_start is not None or window_end is not None:
                try:
                    ts = _parse_ts(m.group("ts"))
                except ValueError:
                    ts = None
                if ts is not None:
                    if window_start is not None and ts < window_start:
                        continue
                    if window_end is not None and ts > window_end + end_grace:
                        continue
            try:
                payload = ast.literal_eval(m.group("payload"))
            except (ValueError, SyntaxError):
                continue
            usage_metadata = payload.get("usage_metadata") if isinstance(payload, dict) else None
            if not isinstance(usage_metadata, dict):
                continue
            result.add(m.group("purpose"), usage_metadata)

    return result
