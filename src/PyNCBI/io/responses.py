"""Detecting what an NCBI reply actually is: bot challenges, private records, 404 pages."""

from __future__ import annotations

import re

_GEO_PRIVATE_RE = re.compile(
    r"Accession\s+(G[A-Z]{2}\d+)\s+is\s+currently\s+private|"
    r"scheduled\s+to\s+be\s+released\s+on\s+([A-Za-z0-9,\s]+)",
    re.IGNORECASE,
)


_GEO_NOT_FOUND_RE = re.compile(
    r"Could\s+not\s+find\s+a\s+public\s+Series\s+or\s+Sample|"
    r"Accession\s+(G[A-Z]{2}\d+)\s+was\s+deleted",
    re.IGNORECASE,
)


_BOT_CHALLENGE_RE = re.compile(
    r"recaptcha/challengepage|RecaptchaChallengePageUi|g-recaptcha|cf-challenge|"
    r"Checking\s+your\s+browser\s+before\s+accessing",
    re.IGNORECASE,
)


def looks_like_bot_challenge(text_sample: str) -> bool:
    """Return True when an HTML payload is an interactive bot/CAPTCHA challenge page."""
    if not text_sample:
        return False
    head = text_sample[:4096]
    return bool(_BOT_CHALLENGE_RE.search(head)) and "<html" in head.lower()
