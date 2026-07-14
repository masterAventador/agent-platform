"""Create a deterministic broad legacy key for test-only convergence checks."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request


BASE_URL = os.environ.get("LITELLM_BOOTSTRAP_URL", "http://litellm:4000").rstrip("/")
MASTER_KEY = os.environ["LITELLM_MASTER_KEY"]
WORKER_KEY = os.environ["LITELLM_WORKER_API_KEY"]


payload = {
    "key": WORKER_KEY,
    "key_alias": "agent-platform-worker-legacy-test",
    "key_type": "llm_api",
    "models": ["general-purpose"],
    "user_id": "agent-platform-worker",
}
request = urllib.request.Request(
    f"{BASE_URL}/key/generate",
    data=json.dumps(payload).encode("utf-8"),
    headers={
        "Authorization": f"Bearer {MASTER_KEY}",
        "Content-Type": "application/json",
    },
    method="POST",
)
try:
    with urllib.request.urlopen(request, timeout=15) as response:
        status = response.status
except urllib.error.HTTPError as exc:
    status = exc.code
if status not in {200, 201}:
    raise SystemExit(f"legacy worker key seed failed (status={status})")
print("Legacy broad worker key seeded for convergence test")
