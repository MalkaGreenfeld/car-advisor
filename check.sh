#!/usr/bin/env bash
# Runs every check of the project, offline and without an API key: the backend and the frontend.
# It stops at the first step that fails, and exits non-zero.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

for tool in uv npm; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "check.sh: '$tool' is not installed. See the prerequisites in README.md." >&2
    exit 1
  fi
done

# Angular's CLI asks about usage statistics on its first run, and waits for an answer.
export NG_CLI_ANALYTICS=false
export CI=1

echo "== backend =="
(
  cd backend
  uv run ruff check
  uv run ruff format --check
  uv run pytest
)

echo "== frontend =="
(
  cd frontend
  npm run lint
  npm run format:check
  npx ng test --watch=false
)

echo "All checks passed."
