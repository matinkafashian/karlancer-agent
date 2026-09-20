"""Run one existing reply scan; no proposal submission or model calls."""

import httpx

from karlancer_agent.config import Settings

settings = Settings()
response = httpx.post(
    "http://localhost:8000/v1/jobs/reply-watch",
    headers={
        "X-Internal-API-Key": settings.internal_api_key.get_secret_value(),
        "Idempotency-Key": "diagnostic-reply-20260915",
    },
    timeout=600,
)
print(response.status_code, response.text[:1500])
