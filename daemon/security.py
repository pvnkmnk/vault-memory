# daemon/security.py
import re
import logging

logger = logging.getLogger("vault-memoryd.security")

def sanitize_text(text: str) -> str:
    """
    Sanitize text to prevent prompt injection and context assembly breakages.

    Strips common LLM instruction overrides and Markdown delimiters that
    could be used to spoof context entries or escape the assembly format.
    """
    patterns = [
        # Prompt Injection Patterns
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

        # Context Assembly Delimiters (Prevent Spoofing)
        r"^---\s*$",                # YAML/Context separator
        r"^#{1,6}\s+\[[A-Z]+\]",   # Assembled context headers like ### [PRIMARY]
    ]

    sanitized = text
    injection_count = 0
    for pattern in patterns:
        flags = re.MULTILINE if pattern.startswith("^") else 0
        matches = re.findall(pattern, sanitized, flags=flags)
        if matches:
            injection_count += len(matches)
            sanitized = re.sub(pattern, "[SANITIZED]", sanitized, flags=flags)

    if injection_count > 0:
        logger.warning(
            "Security: %d injection/delimiter pattern(s) sanitized in text",
            injection_count
        )

    return sanitized
