"""Small security helpers shared by API and filesystem boundaries."""

from __future__ import annotations

import re


def safe_exception_text(exc: Exception, *, limit: int = 1000) -> str:
    """Bound diagnostics and redact common credential shapes."""
    message = str(exc)
    message = re.sub(r"sk-[A-Za-z0-9_-]{8,}", "[REDACTED_API_KEY]", message)
    message = re.sub(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", message)
    return f"{type(exc).__name__}: {message[:limit]}"
