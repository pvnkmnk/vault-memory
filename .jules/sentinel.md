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

## 2026-05-07 - SQL Wildcard Injection and Pathological ReDoS via ILIKE
**Vulnerability:** User-controlled input placed inside wildcards for SQL `LIKE` or `ILIKE` clauses allowed wildcard injection and threatened pathological ReDoS-style backtracking/resource exhaustion in the database.
**Learning:** SQL pattern-matching wildcards (like `%` and `_`) must be escaped when user-supplied queries are intended to be literal search matches. Explicitly define an escape character (e.g. `ESCAPE '\'`) and ensure inputs are length-limited to prevent performance degradation on large tables.
**Prevention:** Use a dedicated `sanitize_like_query` helper to truncate inputs and escape standard SQL wildcards (`\`, `%`, and `_`) for all parameterized `LIKE`/`ILIKE` clauses.
