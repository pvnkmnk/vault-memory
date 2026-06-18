"""
Security helpers for the vault-memory daemon.
Includes prompt injection sanitization and specialized logging.
"""

import logging
import re

# Security-specific logger for tracking injection attempts and other threats
security_logger = logging.getLogger("vault-memoryd.security")

def _sanitize_for_context(text: str) -> str:
    """
    Strips known prompt injection patterns and delimiters from untrusted text.
    Replaces matches with [SANITIZED] to prevent instruction override in LLM prompts.
    """
    patterns = [
        r"(?i)ignore\s+previous\s+instructions",
        r"(?i)disregard\s+(?:the\s+)?(?:above|prior|previous)\s+(?:instructions|content)",
        r"(?i)you\s+(?:are\s+)?(?:now|will)\s+(?:be|become|a)\s+",
        r"(?i)system\s*:\s*(?:instruction|prompt|command|directive)",
        r"(?i)<\|endofprompt\|>",
        r"(?i)<\|startofprompt\|>",
        r"(?i)<\|assistant\|>",
        r"(?i)<\|user\|>",
        r"(?i)<\|system\|>",
        r"(?i)<\|im\|>start",
        r"(?i)<\|im\|>end",
        r"(?i)\[INST\]",
        r"(?i)\[/INST\]",
        r"(?i)\[SYS\]",
        r"(?i)\[/SYS\]",
        r"(?i)<\|beginof\w+\|>",
        r"(?i)<\|endof\w+\|>",
    ]
    sanitized = text
    injection_count = 0
    for pattern in patterns:
        matches = re.findall(pattern, sanitized)
        if matches:
            injection_count += len(matches)
            sanitized = re.sub(pattern, '[SANITIZED]', sanitized)

    if injection_count > 0:
        security_logger.warning(
            'Injection pattern detected and stripped: %d pattern(s) in context',
            injection_count
        )

    return sanitized
