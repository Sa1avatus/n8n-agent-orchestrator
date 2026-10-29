"""Masks secrets in text before it is stored or shown.

Only credential shapes are masked; prose, URLs without credentials and code stay unchanged.
"""

from __future__ import annotations

import re

REDACTED = "***"

# postgres(ql)://user:password@host - keeps the scheme, user and host, masks the password.
_POSTGRES_PASSWORD = re.compile(r"(\bpostgres(?:ql)?://[^\s:/@]+:)[^\s@/]+(?=@)", re.IGNORECASE)

# 'Bearer <token>' - keeps the prefix, masks a token of at least 16 characters.
_BEARER_TOKEN = re.compile(r"(\bBearer\s+)[A-Za-z0-9._~+/=-]{16,}", re.IGNORECASE)

# sk- style API keys (OpenAI, Anthropic, LiteLLM).
_SK_KEY = re.compile(r"\bsk-[A-Za-z0-9_-]{16,}")

# Environment-style assignments with no spaces around '=', e.g. API_KEY=..., DB_PASSWORD=...
# Uppercase only, so code such as 'password = get_password()' is not touched.
_ENV_ASSIGNMENT = re.compile(
    r"(?<![A-Za-z0-9_])([A-Z0-9_]*(?:KEY|TOKEN|PASSWORD|SECRET))="
    r"(\"[^\"]*\"|'[^']*'|[^\s,;&\"']+)"
)

# Long base64 or hex blobs right after the words key, token, secret or password.
_KEYWORD_BLOB = re.compile(
    r"(?i)(\b(?:key|token|secret|password)\b[\"']?\s*[:=]?\s*[\"']?)[A-Za-z0-9+/_=-]{32,}"
)


def redact(text: str) -> str:
    result = _POSTGRES_PASSWORD.sub(rf"\1{REDACTED}", text)
    result = _BEARER_TOKEN.sub(rf"\1{REDACTED}", result)
    result = _SK_KEY.sub(REDACTED, result)
    result = _ENV_ASSIGNMENT.sub(rf"\1={REDACTED}", result)
    return _KEYWORD_BLOB.sub(rf"\1{REDACTED}", result)
