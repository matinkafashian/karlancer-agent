"""Print non-secret identity claims from the mounted Codex login."""

import base64
import json
from pathlib import Path


def decode_claims(token: str) -> dict[str, object]:
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


data = json.loads(Path("/data/codex/auth.json").read_text(encoding="utf-8"))
tokens = data.get("tokens", {})
identity = tokens.get("id_token") or data.get("id_token")
if not isinstance(identity, str) or identity.count(".") < 2:
    print(json.dumps({"login": "ChatGPT", "email": None}))
else:
    claims = decode_claims(identity)
    auth = claims.get("https://api.openai.com/auth", {})
    account_id = auth.get("chatgpt_account_id") if isinstance(auth, dict) else None
    print(
        json.dumps(
            {
                "login": "ChatGPT",
                "email": claims.get("email"),
                "account_id": account_id,
            }
        )
    )
