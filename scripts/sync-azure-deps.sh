#!/usr/bin/env bash
# Regenerate deploy/azure_function/requirements.txt from uv.lock.
# Run after touching pyproject.toml dependencies; CI fails if it's stale.
set -euo pipefail
cd "$(dirname "$0")/.."
uv export \
  --extra azure \
  --no-hashes \
  --no-emit-project \
  --no-dev \
  -o deploy/azure_function/requirements.txt
echo "✓ deploy/azure_function/requirements.txt regenerated"
