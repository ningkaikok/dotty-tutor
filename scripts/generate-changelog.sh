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

# With no explicit range, refresh release tags and generate only commits after
# the latest version. A stale local clone must not re-add already released
# changes to Unreleased. Advanced callers can still pass a range such as
# `v0.3.0..HEAD`, in which case the script does not access the network.
range_args=("$@")
if (( ${#range_args[@]} == 0 )); then
  if git remote get-url origin >/dev/null 2>&1; then
    git fetch --tags --quiet origin
  fi
  latest_tag="$(git describe --tags --abbrev=0 --match 'v[0-9]*' 2>/dev/null || true)"
  if [[ -z "$latest_tag" ]]; then
    echo "No release tag found. Pass an explicit git range, for example v0.1.0..HEAD." >&2
    exit 1
  fi
  range_args=("${latest_tag}..HEAD")
fi

git-cliff --config cliff.toml --unreleased --strip header --output "$generated_file" "${range_args[@]}"
python3 scripts/merge-changelog.py "$generated_file" CHANGELOG.md
echo "CHANGELOG.md generated from Conventional Commits."
