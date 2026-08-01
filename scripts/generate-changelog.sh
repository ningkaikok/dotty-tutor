#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

if ! command -v git-cliff >/dev/null 2>&1; then
  echo "git-cliff is required. Install it from https://git-cliff.org/docs/installation" >&2
  exit 1
fi

generated_file="$(mktemp)"
trap 'rm -f "$generated_file"' EXIT

git-cliff --config cliff.toml --unreleased --strip header --output "$generated_file" "$@"
python3 scripts/merge-changelog.py "$generated_file" CHANGELOG.md
echo "CHANGELOG.md generated from Conventional Commits."
