import logging
import re

security_logger = logging.getLogger("vault-memoryd.security")

def _sanitize_for_context(text: str) -> str:
    """
    Sanitize text to prevent prompt injection when it's included in an LLM context.
    Identifies and redacts known instruction-override patterns.
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
            'Injection pattern detected and stripped: %d pattern(s) in context', injection_count
        )
    return sanitized
