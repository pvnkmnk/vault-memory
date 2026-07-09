# daemon/helpers/security.py
"""Security helpers for sanitization and injection prevention."""

import logging
import re

# Centralized security logger
security_logger = logging.getLogger("vault-memoryd.security")

# Pre-compiled regex for common prompt injection and system control patterns
# Combining patterns into a single regex and using re.subn for single-pass sanitization.
_INJECTION_PATTERN = re.compile(
    r"("
    r"ignore\s+previous\s+instructions|"
    r"disregard\s+(?:the\s+)?(?:above|prior|previous)\s+(?:instructions|content)|"
    r"you\s+(?:are\s+)?(?:now|will)\s+(?:be|become|a)\s+|"
    r"system\s*:\s*(?:instruction|prompt|command|directive)|"
    r"<\|endofprompt\|>|"
    r"<\|startofprompt\|>|"
    r"<\|assistant\|>|"
    r"<\|user\|>|"
    r"<\|system\|>|"
    r"<\|im\|>start|"
    r"<\|im\|>end|"
    r"\[INST\]|"
    r"\[/INST\]|"
    r"\[SYS\]|"
    r"\[/SYS\]|"
    r"<\|beginof\w+\|>|"
    r"<\|endof\w+\|>"
    r")",
    re.IGNORECASE,
)


def sanitize_for_context(text: str) -> str:
    """
    Sanitize text to prevent prompt injection and system command leakage.
    Uses a single-pass regex replacement for efficiency.
    """
    if not text:
        return text

    sanitized, count = _INJECTION_PATTERN.subn("[SANITIZED]", text)

    if count > 0:
        security_logger.warning(
            "Prompt injection or system control pattern detected and sanitized: %d match(es)",
            count
        )

    return sanitized
