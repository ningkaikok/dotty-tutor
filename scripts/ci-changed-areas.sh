#!/usr/bin/env bash
# Decide which CI jobs a change needs. Prints key=value lines (append to $GITHUB_OUTPUT).
#
# Usage: scripts/ci-changed-areas.sh <base-sha> [head]  # pull request: diff base...head
#        scripts/ci-changed-areas.sh --all           # push to main: run everything
#
# Skipping is only for areas a change cannot affect. CI config and shared scripts
# re-run everything so a change to the gates is itself gated.
set -euo pipefail

if [[ "${1:-}" == "--all" ]]; then
  for key in backend frontend e2e docker; do echo "$key=true"; done
  exit 0
fi

base="${1:?usage: ci-changed-areas.sh <base-sha> [head]|--all}"
head="${2:-HEAD}"  # optional, for checking the rules against historical commits
files="$(git diff --name-only "$base"..."$head")"
printf 'changed files:\n%s\n' "$files" >&2

match() { if printf '%s\n' "$files" | grep -Eq "$1"; then echo true; else echo false; fi; }

shared='^(\.github/workflows/ci\.yml|scripts/)'
web_deps='^(package\.json|pnpm-(lock|workspace)\.yaml)$'
echo "backend=$(match "^apps/api/|$shared")"
# Frontend types are generated from the backend OpenAPI, so backend changes re-run check:api.
echo "frontend=$(match "^apps/(web|api)/|$web_deps|$shared")"
echo "e2e=$(match "^apps/web/|$web_deps|$shared")"
echo "docker=$(match "(^|/)Dockerfile|^compose\.ya?ml$|^deploy/|nginx|^apps/api/(pyproject\.toml|uv\.lock)$|^apps/web/package\.json$|$web_deps|$shared")"
