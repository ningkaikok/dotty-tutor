#!/usr/bin/env python3
"""Replace only the Keep a Changelog Unreleased section."""

from pathlib import Path
import sys


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: merge-changelog.py GENERATED CHANGELOG")

    generated_path = Path(sys.argv[1])
    changelog_path = Path(sys.argv[2])
    generated = generated_path.read_text(encoding="utf-8").strip()
    current = changelog_path.read_text(encoding="utf-8")

    marker = "## [Unreleased]"
    start = current.find(marker)
    if start < 0:
        raise SystemExit(f"{changelog_path} does not contain {marker}")

    next_release = current.find("\n## [", start + len(marker))
    if next_release < 0:
        next_release = len(current)

    if generated.startswith(marker):
        replacement = generated
    else:
        replacement = f"{marker}\n"

    before = current[:start]
    after = current[next_release:]
    changelog_path.write_text(
        f"{before}{replacement.rstrip()}\n\n{after.lstrip(chr(10))}",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
