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

## 2026-05-18 - SQL Wildcard DoS and SSRF Callback Vulnerabilities
**Vulnerability:** SQL wildcard parameters inside `/search_siblings` and `/temporal` ILIKE queries were unescaped and unlimited in length, exposing the database to wildcard injection and resource exhaustion attacks. In addition, the bulk queue `callback_url` parameter lacked input validation, representing an SSRF vector if ever invoked.
**Learning:** SQL wildcard characters (`%`, `_`, `\`) used inside `LIKE` or `ILIKE` statements must be escaped to prevent database resource exhaustion. API callback endpoints must validate incoming URLs (enforcing scheme, port, hostname, and rejecting local/private loopbacks) as a first line of defense against SSRF.
**Prevention:** Use a dedicated `sanitize_like_query` helper to escape wildcards and limit length. Always implement strict Pydantic field validators with `urllib` and `ipaddress` for remote callback URLs.
