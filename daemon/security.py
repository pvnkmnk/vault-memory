# daemon/security.py
import re
import logging

logger = logging.getLogger("vault-memoryd.security")

# Pre-compiled patterns for performance (cached at import time)
# Injection patterns: prompt injection attempts that could override LLM instructions
_INJECTION_PATTERNS, _STRUCTURAL_PATTERNS = _build_pattern_lists()


def _build_pattern_lists():
    """Build and cache pattern lists for performance."""
    return (
        [
            re.compile(r"(?i)ignore\s+previous\s+instructions"),
            re.compile(r"(?i)disregard\s+(?:the\s+)?(?:above|prior|previous)\s+(?:instructions|content)"),
            re.compile(r"(?i)you\s+(?:are\s+)?(?:now|will)\s+(?:be|become|a)\s+(?:an?\s+)?(?:assistant|ai|agent|system|model|llm|claude|gemini|gpt|chatbot)s?\b"),
            re.compile(r"(?i)system\s*:\s*(?:instruction|prompt|command|directive)"),
            re.compile(r"(?i)<\|endofprompt\|>"),
            re.compile(r"(?i)<\|startofprompt\|>"),
            re.compile(r"(?i)<\|assistant\|>"),
            re.compile(r"(?i)<\|user\|>"),
            re.compile(r"(?i)<\|system\|>"),
            re.compile(r"(?i)<\|im\|>start"),
            re.compile(r"(?i)<\|im\|>end"),
            re.compile(r"(?i)\[INST\]"),
            re.compile(r"(?i)\[/INST\]"),
            re.compile(r"(?i)\[SYS\]"),
            re.compile(r"(?i)\[/SYS\]"),
            re.compile(r"(?i)<\|beginof\w+\|>"),
            re.compile(r"(?i)<\|endof\w+\|>"),
        ],
        [
            re.compile(r"^---\s*$", re.MULTILINE),
            re.compile(r"^#{1,6}\s+\[[A-Z]+\]", re.MULTILINE),
        ],
    )


def sanitize_for_context(text: str) -> str:
    """
    Sanitize text for context assembly to prevent spoofing context structure.

    Removes BOTH injection patterns (prompt injection attempts) AND structural
    delimiters (---, ### [PRIMARY]) that could be used to fake context entries
    or escape the accordion assembly format.
    """
    sanitized = text
    injection_count = 0

    for pattern in _INJECTION_PATTERNS:
        sanitized, count = pattern.subn("[SANITIZED]", sanitized)
        injection_count += count

    for pattern in _STRUCTURAL_PATTERNS:
        sanitized, count = pattern.subn("[SANITIZED]", sanitized)
        injection_count += count

    if injection_count > 0:
        logger.warning(
            "Security: %d injection/delimiter pattern(s) sanitized in text",
            injection_count
        )

    return sanitized


def sanitize_for_llm(text: str) -> str:
    """
    Sanitize text before sending to LLM (Ollama) for entity extraction.

    Only removes injection patterns that could override LLM instructions.
    Structural patterns (like --- or ### [PRIMARY]) are NOT removed here -
    they are valid Markdown/frontmatter separators and removing them would
    reduce entity extraction quality. See sanitize_for_context() for text
    that needs both injection and structural sanitization.
    """
    sanitized = text
    injection_count = 0

    for pattern in _INJECTION_PATTERNS:
        sanitized, count = pattern.subn("[SANITIZED]", sanitized)
        injection_count += count

    if injection_count > 0:
        logger.warning(
            "Security: %d injection pattern(s) sanitized before LLM call",
            injection_count
        )

    return sanitized
