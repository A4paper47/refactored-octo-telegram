"""Security-oriented logging helpers.

- Redacts Telegram bot tokens if they appear in logs.
- Lowers noisy HTTP client logs that can include tokens in URLs.
"""

from __future__ import annotations

import logging
import re
from typing import Any


# Matches: .../bot<digits>:<token>/...
_TOKEN_RE = re.compile(r"(bot)(\d+):([A-Za-z0-9_-]{20,})")


def _redact_text(s: str) -> str:
    return _TOKEN_RE.sub(r"\1\2:<REDACTED>", s)


class RedactTelegramTokenFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if isinstance(record.msg, str):
                record.msg = _redact_text(record.msg)
            if record.args:
                # Redact any string args.
                if isinstance(record.args, tuple):
                    record.args = tuple(_redact_text(a) if isinstance(a, str) else a for a in record.args)
                elif isinstance(record.args, dict):
                    record.args = {k: (_redact_text(v) if isinstance(v, str) else v) for k, v in record.args.items()}
            # Redact exception text if present.
            if record.exc_info and record.exc_text:
                record.exc_text = _redact_text(record.exc_text)
        except Exception:
            pass
        return True


_INSTALLED = False


def install_security_logging() -> None:
    """Install redaction + reduce noisy HTTP logging.

    Safe to call multiple times.
    """
    global _INSTALLED
    if _INSTALLED:
        return

    # Add token redaction filter to root handlers.
    root = logging.getLogger()
    flt = RedactTelegramTokenFilter()
    try:
        root.addFilter(flt)
    except Exception:
        pass
    for h in root.handlers:
        try:
            h.addFilter(flt)
        except Exception:
            pass

    # Reduce noisy client logs that can include sensitive URLs.
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)

    _INSTALLED = True
