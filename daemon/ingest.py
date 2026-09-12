# daemon/ingest.py
"""S32-1 human ingestion inbox — docs/links/resources into the knowledge base.

Agent sessions are not the only way knowledge should enter the vault. This is
the human half: drop a file in ``inbox/`` (or POST/CLI a path, URL, or pasted
text) and it is

1. **fetched** — local md/txt/pdf, a URL (readability-extracted to markdown),
   or pasted text;
2. **archived** immutably under ``raw/{date}-{slug}.md`` with provenance
   frontmatter, so the compiled wiki page always has an auditable source;
3. **compiled** — an LLM distils the source into entity/concept pages that
   weave ``[[wikilinks]]`` into the existing graph and extract triples via the
   same persistence path ``/cognify`` uses;
4. **recorded** in a manifest keyed by content hash, so a re-run processes only
   the delta instead of recompiling everything.

Claims are tagged ``extracted`` / ``inferred`` / ``ambiguous`` so lint can flag
a page drifting into speculation. A source that collides with a high-trust page
is flagged for review, never silently overwritten.

Everything is best-effort with injectable I/O (``llm``, ``client``) so the
pipeline is testable without network access or a model.
"""

import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("vault-memoryd.ingest")

INBOX_DIRNAME = "inbox"
RAW_DIRNAME = "raw"
MANIFEST_NAME = ".ingest-manifest.json"
KNOWLEDGE_DIRNAME = "Knowledge"
DRAFTS_DIRNAME = "_working/ingest"

#: Claim provenance tags. ``ambiguous`` is what lint watches for.
CLAIM_TAGS = ("extracted", "inferred", "ambiguous")

LLMCallable = Callable[[str, Optional[str]], Awaitable[str]]

TEXT_SUFFIXES = (".md", ".markdown", ".txt", ".text", ".rst")
PDF_SUFFIXES = (".pdf",)

#: Pasted text is unbounded input; cap it rather than letting one paste blow up
#: the prompt (and the archive).
MAX_SOURCE_CHARS = 400_000
MAX_URL_BYTES = 5_000_000

#: ``POST /ingest`` accepts a URL, which means an API-key holder could otherwise
#: make the daemon fetch cloud metadata endpoints (169.254.169.254) or services
#: on its own network. Private/loopback/link-local destinations are refused
#: unless this is set, e.g. to ingest from a wiki on the LAN.
ALLOW_PRIVATE_URLS_ENV = "INGEST_ALLOW_PRIVATE_URLS"

#: Optional allowlist (comma-separated hosts). When set, *only* these hosts may
#: be fetched — for a shared daemon this is strictly stronger than the denylist.
ALLOWED_URL_HOSTS_ENV = "INGEST_URL_ALLOWLIST"

#: Hostnames that never resolve somewhere public.
_BLOCKED_HOSTNAMES = frozenset(
    {"localhost", "localhost.localdomain", "metadata.google.internal", "metadata"}
)
_BLOCKED_HOST_SUFFIXES = (".localhost", ".local", ".internal", ".localdomain")


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------

@dataclass
class Source:
    """A fetched, self-contained input to the ingest pipeline."""

    kind: str  # "text" | "file" | "url"
    ref: str  # absolute path, URL, or "pasted"
    title: str
    content: str
    content_hash: str = ""
    media_type: str = "text/markdown"
    fetched_at: str = ""

    def __post_init__(self) -> None:
        # Normalise *before* hashing: the hash is the delta key, so it has to
        # describe exactly the bytes that get archived.
        self.content = (self.content or "").strip()
        if len(self.content) > MAX_SOURCE_CHARS:
            self.content = self.content[:MAX_SOURCE_CHARS].rstrip() + "\n\n[truncated]"
        if not self.content_hash:
            self.content_hash = content_hash(self.content)
        if not self.fetched_at:
            self.fetched_at = datetime.now(timezone.utc).isoformat()
        self.title = (self.title or "").strip() or "Untitled source"


def content_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8", errors="replace")).hexdigest()


def slugify(value: str, fallback: str = "source") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")
    return slug[:80] or fallback


def looks_like_url(value: str) -> bool:
    return bool(re.match(r"^https?://", (value or "").strip(), re.IGNORECASE))


class IngestError(Exception):
    """A source could not be fetched. Carries a caller-safe message."""

    def __init__(self, message: str, code: str = "INGEST_ERROR") -> None:
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------------------
# Fetching: local files
# ---------------------------------------------------------------------------

def fetch_local_file(path: Path) -> Source:
    """Read a local markdown/text file, or a PDF when the extra is installed."""
    path = Path(path)
    if not path.is_file():
        raise IngestError("Source file not found", code="SOURCE_NOT_FOUND")

    suffix = path.suffix.lower()
    if suffix in PDF_SUFFIXES:
        content = _read_pdf(path)
        media_type = "application/pdf"
    elif suffix in TEXT_SUFFIXES or suffix == "":
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            raise IngestError("Could not read source file", code="SOURCE_UNREADABLE")
        media_type = "text/markdown"
    else:
        raise IngestError(
            f"Unsupported file type '{suffix}'. Supported: {', '.join(TEXT_SUFFIXES + PDF_SUFFIXES)}",
            code="UNSUPPORTED_SOURCE",
        )

    return Source(
        kind="file",
        ref=str(path.resolve()),
        title=_title_from_content(content, path.stem),
        content=content,
        media_type=media_type,
    )


def _read_pdf(path: Path) -> str:
    """PDF text extraction, degrading with an actionable message."""
    try:
        from pypdf import PdfReader  # type: ignore
    except ImportError:
        raise IngestError(
            "PDF ingestion needs the optional dependency: pip install 'vault-memory[ingest]'",
            code="PDF_SUPPORT_MISSING",
        )
    try:
        reader = PdfReader(str(path))
        pages = [(page.extract_text() or "") for page in reader.pages]
    except Exception as e:  # noqa: BLE001 - any malformed PDF is the same to the caller
        logger.warning("PDF extraction failed for %s: %s", path, e)
        raise IngestError("Could not extract text from the PDF", code="SOURCE_UNREADABLE")
    text = "\n\n".join(p.strip() for p in pages if p.strip())
    if not text:
        raise IngestError("The PDF contains no extractable text", code="SOURCE_EMPTY")
    return text


# ---------------------------------------------------------------------------
# Fetching: URLs (readability-style extraction, no extra dependency)
# ---------------------------------------------------------------------------

#: Containers whose text is chrome, not content.
_DROP_TAGS = {"script", "style", "noscript", "nav", "header", "footer", "aside", "form", "svg", "template"}

_HEADING_TAGS = {"h1": "# ", "h2": "## ", "h3": "### ", "h4": "#### ", "h5": "##### ", "h6": "###### "}


class _ReadableExtractor(HTMLParser):
    """Collects headings, paragraphs, list items, and code as markdown-ish text.

    A purpose-built parser rather than a new dependency: the repo ships nothing
    for readability extraction, and pulling in ``readability-lxml`` (plus lxml)
    to convert a page to markdown would be a heavy addition to every install.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: List[Tuple[str, str]] = []
        self.title = ""
        self._skip_depth = 0
        self._buf: List[str] = []
        self._mode: Optional[str] = None
        self._in_title = False

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _DROP_TAGS:
            self._skip_depth += 1
        elif tag == "title":
            self._in_title = True
        elif tag in _HEADING_TAGS:
            self._flush()
            self._mode = "h"
            self._buf = [_HEADING_TAGS[tag]]
        elif tag in ("p", "blockquote"):
            self._flush()
            self._mode = "p"
        elif tag == "li":
            self._flush()
            self._mode = "li"
            self._buf = ["- "]
        elif tag == "pre":
            self._flush()
            self._mode = "pre"

    def handle_endtag(self, tag: str) -> None:
        if tag in _DROP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag == "title":
            self._in_title = False
        elif tag in _HEADING_TAGS or tag in ("p", "blockquote", "li", "pre"):
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
            return
        if self._skip_depth or not self._mode:
            return
        self._buf.append(data)

    def _flush(self) -> None:
        if self._mode is None:
            return
        text = re.sub(r"[ \t\r\f\v]+", " ", "".join(self._buf)).strip()
        if text:
            self.blocks.append((self._mode, text))
        self._buf = []
        self._mode = None

    def close(self) -> None:  # noqa: D102 - HTMLParser contract
        super().close()
        self._flush()

    def markdown(self) -> str:
        lines: List[str] = []
        for mode, text in self.blocks:
            if mode == "h":
                lines.extend([text, ""])
            elif mode == "li":
                lines.append(text if text.startswith("- ") else "- " + text)
            elif mode == "pre":
                lines.extend(["```", text, "```", ""])
            else:
                lines.extend([text, ""])
        return "\n".join(lines).strip()


def extract_readable(html: str) -> Tuple[str, str]:
    """``(title, markdown)`` from an HTML document."""
    parser = _ReadableExtractor()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception as e:  # noqa: BLE001 - malformed HTML must not kill ingest
        logger.warning("HTML extraction failed: %s", e)

    markdown = parser.markdown()
    title = " ".join(parser.title.split()).strip()
    if not title:
        for mode, text in parser.blocks:
            if mode == "h":
                title = text.lstrip("# ").strip()
                break
    return title or "Untitled source", markdown


def _ip_is_private(ip: Any) -> bool:
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _host_is_blocked(host: str) -> bool:
    """True for hostnames that cannot legitimately be a public source."""
    host = (host or "").strip().strip("[]").lower().rstrip(".")
    if not host:
        return True
    if host in _BLOCKED_HOSTNAMES or host.endswith(_BLOCKED_HOST_SUFFIXES):
        return True
    import ipaddress

    try:
        return _ip_is_private(ipaddress.ip_address(host))
    except ValueError:
        return False  # a normal hostname; the DNS check below decides


async def assert_url_is_public(url: str) -> None:
    """Refuse URLs that point at the daemon's own network or cloud metadata.

    Two layers: the literal host, then every address it resolves to. A failed
    lookup is *allowed* through — the request itself will fail on connect, and
    treating an unresolvable name as hostile would make offline environments
    unable to ingest anything. That does leave a narrow DNS-rebinding window
    between this check and the request; closing it would need connection-level
    pinning, which is out of scope for a local-first daemon.
    """
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise IngestError("Only http(s) URLs can be ingested", code="INVALID_SOURCE")

    host = (parts.hostname or "").lower()

    # An explicit allowlist short-circuits every other check: it is both
    # stricter and the thing an operator running a shared daemon actually
    # wants. Matched on the hostname only, so a port or path cannot smuggle a
    # different destination past it.
    allowlist = [h.strip().lower() for h in (os.getenv(ALLOWED_URL_HOSTS_ENV) or "").split(",") if h.strip()]
    if allowlist:
        if host not in allowlist:
            raise IngestError(
                "Host is not in " + ALLOWED_URL_HOSTS_ENV,
                code="URL_NOT_ALLOWED",
            )
        return

    if (os.getenv(ALLOW_PRIVATE_URLS_ENV) or "").strip().lower() in ("1", "true", "on", "yes"):
        return

    if _host_is_blocked(host):
        raise IngestError(
            "Refusing to fetch a private or loopback address",
            code="URL_NOT_ALLOWED",
        )

    import asyncio
    import ipaddress
    import socket

    try:
        loop = asyncio.get_running_loop()
        infos = await loop.getaddrinfo(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80))
    except (OSError, socket.gaierror, ValueError):
        return

    for info in infos or []:
        try:
            address = ipaddress.ip_address(info[4][0])
        except (ValueError, IndexError):
            continue
        if _ip_is_private(address):
            raise IngestError(
                "Refusing to fetch a host that resolves to a private address",
                code="URL_NOT_ALLOWED",
            )


async def fetch_url(url: str, *, client: Any = None, timeout: float = 20.0) -> Source:
    """Fetch a URL and extract its readable text as markdown."""
    url = (url or "").strip()
    if not looks_like_url(url):
        raise IngestError("Not an http(s) URL", code="INVALID_SOURCE")
    await assert_url_is_public(url)

    import httpx

    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=timeout, follow_redirects=True)
    try:
        # Fetching a caller-supplied URL is the feature, not a slip: this is a
        # local-first daemon behind an API key whose documented job is "ingest
        # this link". assert_url_is_public above is the guard — an operator-set
        # allowlist (INGEST_URL_ALLOWLIST) when configured, otherwise a denylist
        # of loopback/private/link-local/reserved destinations checked literally
        # and after DNS. A hardcoded allowlist of hosts would make the feature
        # unusable for the default single-user install.
        # codeql[py/full-ssrf]
        # lgtm[py/full-ssrf]
        response = await client.get(url)
        response.raise_for_status()
        body = response.text
    except Exception as e:  # noqa: BLE001 - network failures are all caller-facing
        logger.warning("URL fetch failed for %s: %s", url, e)
        raise IngestError("Could not fetch the URL", code="SOURCE_UNREACHABLE")
    finally:
        if owns_client:
            await client.aclose()

    if len(body) > MAX_URL_BYTES:
        body = body[:MAX_URL_BYTES]

    title, markdown = extract_readable(body)
    if not markdown.strip():
        # Not HTML (a raw .md/.txt URL): keep the text as-is.
        markdown = body.strip()
    if not markdown.strip():
        raise IngestError("The URL contained no readable text", code="SOURCE_EMPTY")

    return Source(
        kind="url",
        ref=url,
        title=title,
        content=markdown,
        media_type="text/markdown",
    )


def _title_from_content(content: str, fallback: str) -> str:
    for line in (content or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip() or fallback
        if stripped:
            return stripped[:120]
    return fallback


def safe_relative_parts(rel: Any) -> List[str]:
    """Split a user-supplied path into components that cannot escape the root.

    Every component is required to be its own basename (``os.path.basename(p)
    == p``), which rejects absolute paths, ``..``, separators smuggled inside a
    component, and drive letters. Building the path from validated components —
    rather than normalising and then checking — is what makes the containment a
    property of the construction instead of a follow-up assertion.
    """
    raw = str(rel or "").replace("\\", "/")
    parts: List[str] = []
    for part in raw.split("/"):
        if part in ("", "."):
            continue
        if os.path.basename(part) != part or os.path.sep in part or part == "..":
            raise IngestError(
                "Source path is outside the vault", code="UNAUTHORIZED_SOURCE"
            )
        parts.append(part)
    if not parts:
        raise IngestError("No source given", code="INVALID_SOURCE")
    return parts


def resolve_local_source(value: str, vault_root: Path) -> Path:
    """Resolve a local source path, refusing anything outside the vault.

    Enforced here rather than only at the HTTP boundary: the pipeline writes an
    archive of whatever it reads into the vault, so a caller that skipped the
    route check must not be able to turn this into an arbitrary file read.

    Only vault-relative paths are accepted — there is deliberately no absolute
    branch, so no user-supplied absolute path reaches the filesystem at all.
    ``resolve()`` runs *after* the components are validated, so a symlink whose
    target lies outside the vault is rejected as well.
    """
    root = Path(vault_root).expanduser().resolve()
    if Path(value).expanduser().is_absolute():
        raise IngestError(
            "Source path must be relative to the vault", code="UNAUTHORIZED_SOURCE"
        )

    resolved = root.joinpath(*safe_relative_parts(value))
    try:
        real = resolved.resolve()
    except OSError:
        raise IngestError("Source file not found", code="SOURCE_NOT_FOUND")
    if not str(real).startswith(str(root) + os.path.sep):
        raise IngestError(
            "Source path is outside the vault", code="UNAUTHORIZED_SOURCE"
        )
    if not real.is_file():
        raise IngestError("Source file not found", code="SOURCE_NOT_FOUND")
    return real


async def fetch_source(
    value: str,
    *,
    text: Optional[str] = None,
    client: Any = None,
    vault_root: Optional[Path] = None,
) -> Source:
    """Fetch a path, URL, or pasted text into a :class:`Source`.

    ``vault_root`` confines local reads; URLs are checked for private targets.
    """
    if text is not None:
        if not text.strip():
            raise IngestError("Pasted text is empty", code="SOURCE_EMPTY")
        return Source(kind="text", ref="pasted", title=_title_from_content(text, "Pasted text"), content=text)

    value = (value or "").strip()
    if not value:
        raise IngestError("No source given", code="INVALID_SOURCE")
    if looks_like_url(value):
        return await fetch_url(value, client=client)
    if vault_root is None:
        raise IngestError(
            "A vault root is required to read a local source", code="INVALID_SOURCE"
        )
    return fetch_local_file(resolve_local_source(value, vault_root))


# ---------------------------------------------------------------------------
# Immutable raw archive + manifest
# ---------------------------------------------------------------------------

def raw_dir(vault_root: Path) -> Path:
    return Path(vault_root) / RAW_DIRNAME


def manifest_path(vault_root: Path) -> Path:
    return raw_dir(vault_root) / MANIFEST_NAME


def read_manifest(vault_root: Path) -> Dict[str, Any]:
    """``{sources: {source_ref: {...}}}``. A corrupt manifest degrades to empty."""
    path = manifest_path(vault_root)
    if not path.is_file():
        return {"sources": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("ingest manifest is unreadable; treating as empty")
        return {"sources": {}}
    if not isinstance(data, dict) or not isinstance(data.get("sources"), dict):
        return {"sources": {}}
    return data


def write_manifest(vault_root: Path, manifest: Dict[str, Any]) -> None:
    path = manifest_path(vault_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def is_processed(vault_root: Path, source: Source) -> bool:
    """True when this exact content has already been compiled."""
    entry = read_manifest(vault_root).get("sources", {}).get(source.ref)
    return bool(entry) and entry.get("content_hash") == source.content_hash and bool(entry.get("compiled_at"))


def archive_source(vault_root: Path, source: Source) -> Dict[str, Any]:
    """Write ``raw/{date}-{slug}.md``. Never overwrites: a changed body gets a
    suffixed file so the archive stays an immutable record."""
    root = Path(vault_root)
    directory = raw_dir(root)
    directory.mkdir(parents=True, exist_ok=True)

    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    base = f"{day}-{slugify(source.title)}"
    path = directory / f"{base}.md"
    suffix = 1
    while path.exists():
        try:
            existing = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            break
        if _archived_hash(existing) == source.content_hash:
            return {"path": str(path.relative_to(root)), "created": False, "raw_path": str(path)}
        suffix += 1
        path = directory / f"{base}-{suffix}.md"

    frontmatter = {
        "title": source.title,
        "type": "raw-source",
        "source-kind": source.kind,
        "source-ref": source.ref,
        "source-hash": source.content_hash,
        "media-type": source.media_type,
        "fetched-at": source.fetched_at,
        "maturity": "seed",
        "trust": "medium",
        # Reference material: kept, but not immortal.
        "decay-profile": "reference",
    }
    path.write_text(_render_frontmatter(frontmatter) + "\n" + source.content + "\n", encoding="utf-8")
    return {"path": str(path.relative_to(root)), "created": True, "raw_path": str(path)}


def _strip_frontmatter(text: str) -> str:
    match = re.match(r"^---\n.*?\n---\n?", text or "", re.DOTALL)
    return text[match.end():] if match else (text or "")


_ARCHIVED_HASH_RE = re.compile(r"^source-hash:\s*(?P<hash>[0-9a-f]{64})\s*$", re.MULTILINE)


def _archived_hash(text: str) -> Optional[str]:
    """The content hash an existing archive records, if any.

    Reading the recorded hash is exact; re-deriving it from the body would have
    to guess how much whitespace the archive writer added and would turn a
    whitespace difference into a spurious new file.
    """
    match = _ARCHIVED_HASH_RE.search(text or "")
    return match.group("hash") if match else None


def _render_frontmatter(data: Dict[str, Any]) -> str:
    lines = ["---"]
    for key, value in data.items():
        text = "" if value is None else str(value).replace("\n", " ").strip()
        if text == "" or re.search(r"^[\s>|#\[\]{}&*!%@`\"']|:\s|\s#", text):
            text = '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
        lines.append(f"{key}: {text}")
    lines.append("---")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Compile pass
# ---------------------------------------------------------------------------

COMPILE_SCHEMA = """{
  "summary": "2-4 sentences: what this source actually claims",
  "pages": [
    {
      "title": "Entity or concept name",
      "page_type": "entity" | "concept",
      "body": "markdown body. Weave [[wikilinks]] to entities that already exist.",
      "entities": ["EntityName"],
      "claims": [{"text": "one atomic claim", "tag": "extracted" | "inferred" | "ambiguous"}]
    }
  ],
  "triples": [
    {"subject": "...", "predicate": "...", "object": "..."}
  ]
}"""

COMPILE_PROMPT = """You fold a raw source document into an existing Obsidian knowledge vault.

Rules:
- Only state what the source supports. Tag a claim "extracted" when the source says it
  directly, "inferred" when you had to combine two statements, "ambiguous" when the
  source is unclear or contradicts something.
- Reuse existing page titles when the source is about something already known; do not
  invent a near-duplicate page.
- Do not restate this vault's own operational knowledge. This is source material.

## Source
Title: {title}
Kind: {kind}
Reference: {ref}

{content}

## Pages that already exist for these topics (do NOT duplicate; update instead)
{existing}

## Known high-trust pages (never overwrite these)
{trusted}

Return ONLY JSON matching this shape:
{schema}
"""


def build_compile_prompt(
    source: Source,
    *,
    existing_pages: Optional[List[str]] = None,
    trusted_pages: Optional[List[str]] = None,
) -> str:
    existing = "\n".join("- " + p for p in (existing_pages or [])) or "(none)"
    trusted = "\n".join("- " + p for p in (trusted_pages or [])) or "(none)"
    return COMPILE_PROMPT.format(
        title=source.title,
        kind=source.kind,
        ref=source.ref,
        content=source.content,
        existing=existing,
        trusted=trusted,
        schema=COMPILE_SCHEMA,
    )


def parse_compile_response(text: str) -> Dict[str, Any]:
    """Tolerant parse of the compile response. Never raises."""
    empty: Dict[str, Any] = {"summary": "", "pages": [], "triples": []}
    if not text:
        return empty

    raw: Any = None
    try:
        raw = json.loads(text)
    except (ValueError, TypeError):
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                raw = json.loads(match.group())
            except ValueError:
                logger.warning("compile response was not parseable JSON")
                return empty
    if not isinstance(raw, dict):
        return empty

    pages = []
    for item in raw.get("pages") or []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        body = str(item.get("body") or "").strip()
        if not title or not body:
            continue
        page_type = str(item.get("page_type") or "entity").lower()
        claims = []
        for claim in item.get("claims") or []:
            if not isinstance(claim, dict):
                continue
            text_ = str(claim.get("text") or "").strip()
            tag = str(claim.get("tag") or "inferred").lower()
            if text_:
                claims.append({"text": text_, "tag": tag if tag in CLAIM_TAGS else "inferred"})
        pages.append(
            {
                "title": title,
                "page_type": "concept" if page_type == "concept" else "entity",
                "body": body,
                "entities": [str(e) for e in (item.get("entities") or [])],
                "claims": claims,
            }
        )

    triples = []
    for item in raw.get("triples") or []:
        if not isinstance(item, dict):
            continue
        subject = str(item.get("subject") or "").strip()
        predicate = str(item.get("predicate") or "").strip()
        obj = str(item.get("object") or "").strip()
        if subject and predicate and obj:
            triples.append({"subject": subject, "predicate": predicate, "object": obj})

    return {"summary": str(raw.get("summary") or "").strip(), "pages": pages, "triples": triples}


def claim_tag_counts(pages: List[Dict[str, Any]]) -> Dict[str, int]:
    counts = {tag: 0 for tag in CLAIM_TAGS}
    for page in pages:
        for claim in page.get("claims") or []:
            tag = claim.get("tag")
            if tag in counts:
                counts[tag] += 1
    return counts


def render_claims_section(claims: List[Dict[str, Any]]) -> str:
    """A ``## Claims`` block with provenance, so lint can spot speculation."""
    if not claims:
        return ""
    lines = ["## Claims", "", "| Tag | Claim |", "| --- | --- |"]
    for claim in claims:
        text = str(claim.get("text") or "").replace("|", "\\|")
        lines.append(f"| {claim.get('tag')} | {text} |")
    return "\n".join(lines) + "\n"


def _page_path(vault_root: Path, title: str, page_type: str) -> Path:
    directory = Path(vault_root) / KNOWLEDGE_DIRNAME
    slug = re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-") or "untitled"
    if page_type == "concept":
        return directory / f"concept-{slug}.md"
    return directory / f"{slug}.md"


def _read_page_trust(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    match = re.search(r"^trust:\s*(.+)$", _strip_frontmatter(text[:2000]) or text[:2000], re.MULTILINE)
    return (match.group(1).strip().strip("\"'").lower() if match else "")


def _page_meta(path: Path) -> Dict[str, str]:
    """Minimal frontmatter read for an existing vault page."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    match = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
    if not match:
        return {}
    out = {}
    for line in match.group(1).splitlines():
        if ":" in line and not line.startswith((" ", "\t", "#")):
            key, _, value = line.partition(":")
            out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def existing_knowledge_pages(vault_root: Path) -> Tuple[List[str], List[str]]:
    """``(all page titles, high-trust page titles)`` in ``Knowledge/``."""
    directory = Path(vault_root) / KNOWLEDGE_DIRNAME
    existing: List[str] = []
    trusted: List[str] = []
    if not directory.is_dir():
        return existing, trusted
    for path in sorted(directory.glob("*.md")):
        meta = _page_meta(path)
        title = meta.get("title") or path.stem
        existing.append(title)
        if meta.get("trust", "").lower() == "high":
            trusted.append(title)
    return existing, trusted


def _write_page(
    path: Path,
    *,
    title: str,
    body: str,
    source: Source,
    page_type: str,
    claims: List[Dict[str, Any]],
    raw_path: str,
    updated: bool,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if updated:
        existing_text = ""
        try:
            existing_text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass
        incoming = "\n## Update — " + datetime.now(timezone.utc).strftime("%Y-%m-%d") + "\n\n" + body
        if "## Claims" in incoming:
            incoming = incoming.split("## Claims")[0].rstrip() + "\n"
        path.write_text(existing_text.rstrip() + "\n" + incoming, encoding="utf-8")
        return

    tags = claim_tag_counts([{"claims": claims}])
    frontmatter = {
        "title": title,
        "type": page_type,
        "maturity": "sapling",
        # Ingested material is not reviewed by a human, so it does not get high trust.
        "trust": "medium",
        "decay-profile": "reference",
        "source-kind": source.kind,
        "source-ref": source.ref,
        "source-raw": raw_path,
        "claim-tags": f"extracted={tags['extracted']},inferred={tags['inferred']},ambiguous={tags['ambiguous']}",
        "date_created": datetime.now(timezone.utc).isoformat(),
    }
    claims_section = render_claims_section(claims)
    content = _render_frontmatter(frontmatter) + "\n\n" + body.strip() + "\n"
    if claims_section:
        content += "\n" + claims_section
    path.write_text(content, encoding="utf-8")


async def compile_source(
    deps: Any,
    vault_root: Path,
    source: Source,
    *,
    archived: Optional[Dict[str, Any]] = None,
    llm: Optional[LLMCallable] = None,
) -> Dict[str, Any]:
    """Distil one archived source into wiki pages + triples. Never raises."""
    from daemon.miner import make_default_llm

    llm = llm or make_default_llm(getattr(deps, "settings", None))
    root = Path(vault_root)
    archived = archived or archive_source(root, source)
    raw_rel = archived["path"]

    try:
        existing, trusted = existing_knowledge_pages(root)
        prompt = build_compile_prompt(source, existing_pages=existing, trusted_pages=trusted)
        parsed = parse_compile_response(await llm(prompt, None))

        created: List[str] = []
        updated: List[str] = []
        conflicts: List[Dict[str, Any]] = []
        for page in parsed["pages"]:
            target = _page_path(root, page["title"], page["page_type"])
            if target.exists():
                meta = _page_meta(target)
                if (meta.get("trust") or "").lower() == "high":
                    # Never silently overwrite a high-trust page.
                    conflicts.append(
                        {"title": page["title"], "path": str(target.relative_to(root)), "reason": "high-trust page"}
                    )
                    continue
                _write_page(
                    target,
                    title=page["title"],
                    body=page["body"],
                    source=source,
                    page_type=page["page_type"],
                    claims=page["claims"],
                    raw_path=raw_rel,
                    updated=True,
                )
                updated.append(str(target.relative_to(root)))
            else:
                _write_page(
                    target,
                    title=page["title"],
                    body=page["body"],
                    source=source,
                    page_type=page["page_type"],
                    claims=page["claims"],
                    raw_path=raw_rel,
                    updated=False,
                )
                created.append(str(target.relative_to(root)))

        triples_written = 0
        if parsed["triples"]:
            try:
                from daemon.routes.knowledge import _persist_cognify_triples

                triples_written = _persist_cognify_triples(parsed["triples"], deps).get(
                    "relationships_written", 0
                )
            except Exception as e:  # noqa: BLE001 - graph write must not lose the pages
                logger.warning("ingest triple persistence failed: %s", e)

        return {
            "status": "compiled",
            "raw_path": raw_rel,
            "summary": parsed["summary"],
            "pages_created": created,
            "pages_updated": updated,
            "conflicts": conflicts,
            "triples": len(parsed["triples"]),
            "relationships_written": triples_written,
            "claim_tags": claim_tag_counts(parsed["pages"]),
            "error": None,
        }
    except Exception as e:  # noqa: BLE001 - one bad source must not stop the inbox
        logger.warning("compile failed for %s: %s", source.ref, e)
        return {
            "status": "failed",
            "raw_path": raw_rel,
            "pages_created": [],
            "pages_updated": [],
            "conflicts": [],
            "triples": 0,
            "relationships_written": 0,
            "claim_tags": {tag: 0 for tag in CLAIM_TAGS},
            "error": str(e)[:500],
        }


async def ingest(
    deps: Any,
    vault_root: Path,
    value: str = "",
    *,
    text: Optional[str] = None,
    client: Any = None,
    llm: Optional[LLMCallable] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """Fetch → archive → compile one source. Records the delta in the manifest."""
    root = Path(vault_root)
    try:
        source = await fetch_source(value, text=text, client=client, vault_root=root)
    except IngestError as e:
        return {"status": "failed", "error": str(e), "code": e.code, "source": value or "pasted"}

    manifest = read_manifest(root)
    entry = manifest.get("sources", {}).get(source.ref)
    if not force and entry and entry.get("content_hash") == source.content_hash and entry.get("compiled_at"):
        return {
            "status": "skipped",
            "reason": "unchanged",
            "source": source.ref,
            "raw_path": entry.get("raw_path"),
            "pages_created": [],
            "pages_updated": [],
        }

    archived = archive_source(root, source)
    result = await compile_source(deps, root, source, archived=archived, llm=llm)

    manifest.setdefault("sources", {})[source.ref] = {
        "content_hash": source.content_hash,
        "title": source.title,
        "kind": source.kind,
        "raw_path": archived["path"],
        "archived_at": source.fetched_at,
        "compiled_at": datetime.now(timezone.utc).isoformat() if result["status"] == "compiled" else None,
        "pages": result["pages_created"] + result["pages_updated"],
        "conflicts": result["conflicts"],
        "claim_tags": result["claim_tags"],
    }
    try:
        write_manifest(root, manifest)
    except OSError as e:
        logger.warning("could not write the ingest manifest: %s", e)

    # Index everything this source produced so /search finds it immediately.
    await _index_paths(deps, root, archived["raw_path"], result["pages_created"] + result["pages_updated"])

    return {"source": source.ref, "title": source.title, **result}


async def _index_paths(deps: Any, vault_root: Path, *rel_paths: Any) -> None:
    watcher = getattr(deps, "watcher", None)
    engine = getattr(watcher, "engine", None) if watcher else None
    if engine is None:
        return
    for rel in rel_paths:
        if not rel:
            continue
        try:
            await engine.sync_file(Path(vault_root) / rel, caller="user")
        except Exception as e:  # noqa: BLE001 - indexing is not the ingest's job to guarantee
            logger.warning("could not index %s after ingest: %s", rel, e)


def inbox_sources(vault_root: Path) -> List[Path]:
    """Files sitting in ``inbox/``, oldest first.

    ``ensure_inbox`` writes a README explaining the drop point; that is
    documentation, not a source, so it is never ingested.
    """
    directory = Path(vault_root) / INBOX_DIRNAME
    if not directory.is_dir():
        return []
    return sorted(
        (
            p
            for p in directory.rglob("*")
            if p.is_file() and not p.name.startswith(".") and p.name.lower() != "readme.md"
        ),
        key=lambda p: p.name,
    )


async def ingest_inbox(
    deps: Any,
    vault_root: Path,
    *,
    limit: int = 10,
    client: Any = None,
    llm: Optional[LLMCallable] = None,
    force: bool = False,
    remove_after: bool = False,
) -> Dict[str, Any]:
    """Drain ``inbox/``. Each file is archived then compiled."""
    root = Path(vault_root)
    results = []
    for path in inbox_sources(root)[: max(0, limit)]:
        # Vault-relative, matching resolve_local_source's contract.
        try:
            rel = str(path.relative_to(root))
        except ValueError:
            rel = path.name
        result = await ingest(deps, root, rel, client=client, llm=llm, force=force)
        results.append({"file": str(path.relative_to(root)), **result})
        if result["status"] == "compiled" and remove_after:
            try:
                path.unlink()
            except OSError as e:
                logger.warning("ingested %s but could not clear it from the inbox: %s", path, e)

    return {
        "queued": len(results),
        "compiled": sum(1 for r in results if r["status"] == "compiled"),
        "skipped": sum(1 for r in results if r["status"] == "skipped"),
        "failed": sum(1 for r in results if r["status"] == "failed"),
        "results": results,
    }


def ensure_inbox(vault_root: Path) -> Path:
    """Create ``inbox/`` with a README so the drop point is self-explanatory."""
    directory = Path(vault_root) / INBOX_DIRNAME
    directory.mkdir(parents=True, exist_ok=True)
    readme = directory / "README.md"
    if not readme.exists():
        readme.write_text(
            "# Ingestion inbox\n\n"
            "Drop `.md`, `.txt`, or `.pdf` files here, then run:\n\n"
            "```bash\nvault-memory ingest --inbox\n```\n\n"
            "Each file is archived immutably under `raw/` and compiled into "
            "`Knowledge/` pages with claim provenance. Files are left in place so a "
            "failed ingest can be retried; pass `--remove` once they compile cleanly.\n",
            encoding="utf-8",
        )
    return directory
