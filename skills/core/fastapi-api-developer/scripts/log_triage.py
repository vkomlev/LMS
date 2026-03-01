#!/usr/bin/env python3
"""Summarize LMS API logs for fast debugging cycles.

Parses common app.log lines:
- generic leveled logs: "<ts> | <LEVEL> | <logger> | <message>"
- uvicorn access logs inside message:
  '127.0.0.1:1234 - "GET /path HTTP/1.1" 500'
"""

from __future__ import annotations

import argparse
import collections
import pathlib
import re
import sys
from typing import Counter, Iterable

LINE_RE = re.compile(
    r"^\s*(?P<ts>\d{4}-\d{2}-\d{2} [\d:,]+)\s*\|\s*(?P<level>[A-Z]+)\s*\|\s*(?P<logger>[^|]+)\|\s*(?P<msg>.*)$"
)
ACCESS_RE = re.compile(
    r'"(?P<method>[A-Z]+)\s+(?P<path>\S+)\s+HTTP/\d\.\d"\s+(?P<status>\d{3})'
)
ERROR_TOKENS = ("error", "exception", "traceback", "failed", "timeout", "denied")


def read_tail(path: pathlib.Path, max_lines: int) -> list[str]:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if max_lines <= 0 or len(lines) <= max_lines:
        return lines
    return lines[-max_lines:]


def top_items(counter: Counter[str], limit: int) -> list[tuple[str, int]]:
    return counter.most_common(limit)


def is_error_message(msg: str) -> bool:
    lower = msg.lower()
    return any(token in lower for token in ERROR_TOKENS)


def summarize(lines: Iterable[str], top: int) -> str:
    level_counts: Counter[str] = collections.Counter()
    logger_counts: Counter[str] = collections.Counter()
    status_counts: Counter[str] = collections.Counter()
    path_counts: Counter[str] = collections.Counter()
    failing_paths: Counter[str] = collections.Counter()
    error_lines: list[str] = []

    total = 0
    parsed = 0

    for line in lines:
        total += 1
        m = LINE_RE.match(line)
        if not m:
            continue
        parsed += 1
        level = m.group("level").strip()
        logger = m.group("logger").strip()
        msg = m.group("msg").strip()

        level_counts[level] += 1
        logger_counts[logger] += 1

        access = ACCESS_RE.search(msg)
        if access:
            status = access.group("status")
            path = access.group("path")
            status_counts[status] += 1
            path_counts[path] += 1
            if status.startswith("4") or status.startswith("5"):
                failing_paths[f"{status} {path}"] += 1

        if level in {"ERROR", "CRITICAL"} or is_error_message(msg):
            error_lines.append(line)

    parts: list[str] = []
    parts.append("=== Log Triage Summary ===")
    parts.append(f"Total lines scanned: {total}")
    parts.append(f"Structured lines parsed: {parsed}")
    parts.append("")

    parts.append("Levels:")
    for k, v in top_items(level_counts, top):
        parts.append(f"- {k}: {v}")
    if not level_counts:
        parts.append("- no level data found")
    parts.append("")

    parts.append("Top loggers:")
    for k, v in top_items(logger_counts, top):
        parts.append(f"- {k}: {v}")
    if not logger_counts:
        parts.append("- no logger data found")
    parts.append("")

    parts.append("HTTP statuses:")
    for k, v in top_items(status_counts, top):
        parts.append(f"- {k}: {v}")
    if not status_counts:
        parts.append("- no access status data found")
    parts.append("")

    parts.append("Top endpoints:")
    for k, v in top_items(path_counts, top):
        parts.append(f"- {k}: {v}")
    if not path_counts:
        parts.append("- no endpoint data found")
    parts.append("")

    parts.append("Failing endpoints (4xx/5xx):")
    for k, v in top_items(failing_paths, top):
        parts.append(f"- {k}: {v}")
    if not failing_paths:
        parts.append("- none")
    parts.append("")

    parts.append("Recent error-like lines:")
    if error_lines:
        for line in error_lines[-top:]:
            parts.append(f"- {line}")
    else:
        parts.append("- none")

    return "\n".join(parts)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Summarize API log file for debugging.")
    p.add_argument("--log-file", required=True, help="Path to log file.")
    p.add_argument("--tail", type=int, default=3000, help="Number of last lines to analyze.")
    p.add_argument("--top", type=int, default=10, help="Top N groups to show per section.")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    log_path = pathlib.Path(args.log_file)
    if not log_path.exists():
        print(f"[FAIL] log file not found: {log_path}")
        return 2
    lines = read_tail(log_path, max_lines=args.tail)
    print(summarize(lines, top=args.top))
    return 0


if __name__ == "__main__":
    sys.exit(main())

