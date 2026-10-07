#!/usr/bin/env python3
"""Fail without revealing values when tracked files resemble credentials.

Run before committing:

    .Luma/bin/python scripts/check_secrets.py
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


RULES = {
    "Google API key": re.compile(r"AIza[0-9A-Za-z_-]{20,}"),
    "GitHub token": re.compile(r"(?:gh[pousr]_[0-9A-Za-z]{20,}|github_pat_[0-9A-Za-z_]{20,})"),
    "OpenAI API key": re.compile(r"sk-(?:proj-)?[0-9A-Za-z_-]{20,}"),
    "AWS access key": re.compile(r"(?:AKIA|ASIA)[0-9A-Z]{16}"),
    "Private key": re.compile(
        r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
    ),
    "Hard-coded password": re.compile(
        r'''(?ix)\b(?:password|passwd|pwd)\s*=\s*["']
        (?!(?:password|changeme|example|your[_ -]?password)["'])
        [^"'\r\n]{8,}["']'''
    ),
}


def tracked_files() -> list[Path]:
    """Return tracked regular files without relying on shell expansion."""
    result = subprocess.run(
        ["git", "ls-files", "-z"], check=True, capture_output=True
    )
    return [Path(item) for item in result.stdout.decode().split("\0") if item]


def main() -> int:
    findings: list[tuple[str, Path]] = []
    for path in tracked_files():
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for rule_name, pattern in RULES.items():
            if pattern.search(content):
                findings.append((rule_name, path))

    if not findings:
        print("Secret scan passed: no high-confidence credential patterns in tracked files.")
        return 0

    print("Secret scan failed. Rotate any exposed credential and remove it from Git history.")
    for rule_name, path in findings:
        print(f"- {rule_name}: {path} (value redacted)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
