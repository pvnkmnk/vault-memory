## 2026-04-23 - Ripgrep Argument Injection and Flag Conflict
**Vulnerability:** User-supplied queries starting with `-` could inject flags into the `ripgrep` subprocess command. Additionally, the use of `-l` with `--json` suppressed the match data expected by the parser.
**Learning:** Always use the `--` positional argument separator when shelling out to CLI tools with user input. Be aware that some CLI flags (like `-l` and `--json` in `rg`) can be mutually exclusive or change output formats in ways that break parsers.
**Prevention:** Use `--` for all subprocess calls involving user input. Validate CLI tool compatibility when combining flags.

## 2026-04-23 - Information Leakage in Error Responses
**Vulnerability:** `error_response` only hid details if the message started with "Internal", allowing other 5xx errors to potentially leak stack traces or database info via the `detail` field.
**Learning:** Security by string matching is fragile. Use HTTP status codes as the source of truth for when to redact technical details.
**Prevention:** Redact `detail` for all responses where `status_code >= 500`.

## 2026-04-30 - Custom Error Field Information Leakage
**Vulnerability:** Endpoints using custom response structures (like `cognify` and `promote`) were manually returning `str(e)` in error fields, bypassing the global redaction logic in `error_response`.
**Learning:** System-wide security helpers only work if they are used consistently. Custom response formats often introduce security gaps if not designed with the same rigor as standard error paths.
**Prevention:** Always use centralized error handlers (`server_error`) or explicitly redact technical details in custom error fields. Verify redaction with regression tests.

## 2026-06-25 - Prompt Injection Sanitization and Centralization
**Vulnerability:** The `/cognify` endpoint and context assembly pipeline were directly processing untrusted user content without sanitization, risking prompt injection where an attacker could override system instructions.
**Learning:** Security utilities like `_sanitize_for_context` must be centralized and applied at all trust boundaries, especially before sending data to an LLM. Local definitions of security logic lead to inconsistent application and maintenance gaps.
**Prevention:** Move all security-critical sanitization to `daemon/helpers/security.py` and ensure it is called by any endpoint or pipeline that handles user input destined for an LLM context.
