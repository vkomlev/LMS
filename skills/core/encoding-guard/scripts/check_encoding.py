#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable


MOJIBAKE_MARKERS = (
    "Ð",
    "Ñ",
    "РџС",
    "РёР",
    "С‚Р",
    "êà",
    "èñï",
)


TEXT_EXTENSIONS = {
    ".md",
    ".diff",
    ".mdc",
    ".txt",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".ps1",
    ".py",
}


def is_text_candidate(path: Path) -> bool:
    return path.suffix.lower() in TEXT_EXTENSIONS


def iter_files(base: Path, recursive: bool) -> Iterable[Path]:
    if base.is_file():
        yield base
        return
    if recursive:
        for p in base.rglob("*"):
            if p.is_file() and is_text_candidate(p):
                yield p
    else:
        for p in base.glob("*"):
            if p.is_file() and is_text_candidate(p):
                yield p


def check_file(path: Path) -> tuple[str, str]:
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        return ("FAIL", f"not_utf8 ({exc})")

    for marker in MOJIBAKE_MARKERS:
        if marker in text:
            return ("WARN", f"mojibake_marker:{marker}")

    latin1_noise = sum(1 for ch in text if 0x00C0 <= ord(ch) <= 0x00FF)
    if latin1_noise > 0:
        return ("WARN", f"latin1_noise:{latin1_noise}")

    return ("OK", "utf8_clean")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check files for UTF-8 validity and common mojibake markers."
    )
    parser.add_argument("--path", required=True, help="File or directory path")
    parser.add_argument(
        "--recursive", action="store_true", help="Recurse directories"
    )
    args = parser.parse_args()

    target = Path(args.path)
    if not target.exists():
        print(f"ERROR: path does not exist: {target}")
        return 2

    failures = 0
    warnings = 0
    checked = 0

    for file_path in iter_files(target, args.recursive):
        checked += 1
        status, detail = check_file(file_path)
        print(f"[{status}] {file_path} :: {detail}")
        if status == "FAIL":
            failures += 1
        elif status == "WARN":
            warnings += 1

    print(
        f"\nSummary: checked={checked}, warnings={warnings}, failures={failures}"
    )
    if failures > 0:
        return 1
    if warnings > 0:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
