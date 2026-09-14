#!/usr/bin/env bash
# Regenerate deploy/azure_function/requirements.txt and
# deploy/azure_function_external/requirements.txt from uv.lock.
# Run after touching pyproject.toml dependencies; CI fails if either is stale.
set -euo pipefail
cd "$(dirname "$0")/.."
uv export \
  --extra azure \
  --no-hashes \
  --no-emit-project \
  --no-dev \
  -o deploy/azure_function/requirements.txt
cp deploy/azure_function/requirements.txt deploy/azure_function_external/requirements.txt
echo "✓ deploy/azure_function/requirements.txt and deploy/azure_function_external/requirements.txt regenerated"
