#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

uv run --project "${ROOT_DIR}/backend" --frozen pytest -q \
  "${ROOT_DIR}/backend/tests/unit/artifacts/test_storage.py" \
  "${ROOT_DIR}/backend/tests/unit/artifacts/test_service.py" \
  "${ROOT_DIR}/backend/tests/contract/artifacts/test_artifacts.py" \
  "${ROOT_DIR}/backend/tests/integration/database/test_migrations.py" \
  "${ROOT_DIR}/backend/tests/integration/storage/test_real_cos_artifact_storage.py"

# Prove that the controller can materialize the same public 25 MiB boundary
# accepted by the upload API, using a real isolated Docker sandbox.
bash "${ROOT_DIR}/infra/platform/test-local-sandbox.sh"

# C04 reuses the repository's isolated production-like MVP profile: PostgreSQL,
# Redis, MinIO, API, dispatcher, Worker, sandbox controller, LiteLLM Stub and a
# headless Playwright browser. The Tauri GUI is deliberately excluded here.
MVP_PROFILE_SKIP_TAURI=true \
  bash "${ROOT_DIR}/infra/platform/test-mvp-profile.sh"

printf 'C04 real Agent/sandbox/object-storage/database/API/UI acceptance passed\n'
