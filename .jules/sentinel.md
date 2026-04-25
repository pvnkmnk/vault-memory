## 2026-04-23 - Ripgrep Argument Injection and Flag Conflict
**Vulnerability:** User-supplied queries starting with `-` could inject flags into the `ripgrep` subprocess command. Additionally, the use of `-l` with `--json` suppressed the match data expected by the parser.
**Learning:** Always use the `--` positional argument separator when shelling out to CLI tools with user input. Be aware that some CLI flags (like `-l` and `--json` in `rg`) can be mutually exclusive or change output formats in ways that break parsers.
**Prevention:** Use `--` for all subprocess calls involving user input. Validate CLI tool compatibility when combining flags.

## 2026-04-23 - Information Leakage in Error Responses
**Vulnerability:** `error_response` only hid details if the message started with "Internal", allowing other 5xx errors to potentially leak stack traces or database info via the `detail` field.
**Learning:** Security by string matching is fragile. Use HTTP status codes as the source of truth for when to redact technical details.
**Prevention:** Redact `detail` for all responses where `status_code >= 500`.
