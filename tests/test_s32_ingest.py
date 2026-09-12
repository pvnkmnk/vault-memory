# tests/test_s32_ingest.py
"""S32-1 (issue #81): the human ingestion inbox.

Everything runs offline: the URL client and the LLM are injected. Locked down:

- a source is archived immutably under ``raw/`` with provenance frontmatter, and
  re-ingesting the same bytes reuses that archive instead of overwriting it;
- local paths are confined to the vault (the daemon must not be a file-read shim);
- the manifest makes a re-run process only the delta;
- claim provenance is written, and lint flags a page drifting into speculation;
- a high-trust page is reported as a conflict, never overwritten.
"""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from daemon import ingest


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

HTML = """<html><head><title>Real Title</title><style>.x{}</style></head>
<body>
<nav>Home | About</nav>
<article>
  <h1>Heading One</h1>
  <p>First paragraph of the article.</p>
  <ul><li>Item one</li><li>Item two</li></ul>
  <pre>code_block()</pre>
</article>
<footer>Copyright nobody</footer>
<script>track()</script>
</body></html>"""


def _llm(payload):
    text = payload if isinstance(payload, str) else json.dumps(payload)

    async def _call(prompt, model=None):
        return text

    return _call


COMPILE_PAYLOAD = {
    "summary": "A short summary.",
    "pages": [
        {
            "title": "Ingestion Inbox",
            "page_type": "concept",
            "body": "The inbox feeds [[vault-memory]].",
            "entities": ["vault-memory"],
            "claims": [
                {"text": "Sources are archived under raw/", "tag": "extracted"},
                {"text": "Compilation is delta-only", "tag": "inferred"},
                {"text": "PDF support is unclear", "tag": "ambiguous"},
            ],
        }
    ],
    "triples": [{"subject": "inbox", "predicate": "feeds", "object": "vault-memory"}],
}


def _deps(tmp_path):
    return SimpleNamespace(
        postgres=MagicMock(),
        settings=SimpleNamespace(lite_mode=False, vault_path=str(tmp_path)),
        watcher=None,
    )


class _Client:
    """Stub httpx.AsyncClient for URL fetches."""

    def __init__(self, text=HTML, status=200, redirects=None):
        self._text = text
        self._status = status
        #: url -> (status, location) so a redirect chain can be scripted.
        self._redirects = redirects or {}
        self.requested = []

    async def get(self, url, **kwargs):
        assert kwargs.get("follow_redirects") is False, "redirects must be manual"
        self.requested.append(url)
        if url in self._redirects:
            status, location = self._redirects[url]
            return self._response(status, "", {"location": location})
        return self._response(self._status, self._text)

    def _response(self, status, text, headers=None):
        class _Resp:
            status_code = status

            def __init__(self):
                self.headers = headers or {}

            def raise_for_status(self):
                if status >= 400:
                    raise RuntimeError(f"HTTP {status}")

            @property
            def text(self):
                return text

        return _Resp()

    async def aclose(self):
        return None


# ---------------------------------------------------------------------------
# source classification and fetching
# ---------------------------------------------------------------------------

def test_url_detection_and_slugging():
    assert ingest.looks_like_url("https://example.com/a")
    assert ingest.looks_like_url("HTTP://EXAMPLE.COM")
    assert not ingest.looks_like_url("/tmp/file.md")
    assert ingest.slugify("Hello, World! (2026)") == "hello-world-2026"
    assert ingest.slugify("") == "source"
    assert ingest.slugify("!!!") == "source"


def test_fetch_local_markdown_uses_the_heading_as_title(tmp_path):
    path = tmp_path / "doc.md"
    path.write_text("# Real Heading\n\nbody\n", encoding="utf-8")

    source = ingest.fetch_local_file(path)
    assert source.kind == "file"
    assert source.title == "Real Heading"
    assert source.content.startswith("# Real Heading")
    assert source.content_hash == ingest.content_hash(source.content)


def test_fetch_local_rejects_unsupported_types_and_missing_files(tmp_path):
    xlsx = tmp_path / "sheet.xlsx"
    xlsx.write_bytes(b"binary")
    with pytest.raises(ingest.IngestError) as exc:
        ingest.fetch_local_file(xlsx)
    assert exc.value.code == "UNSUPPORTED_SOURCE"

    with pytest.raises(ingest.IngestError) as exc:
        ingest.fetch_local_file(tmp_path / "nope.md")
    assert exc.value.code == "SOURCE_NOT_FOUND"


def test_pdf_without_the_optional_extra_explains_the_fix(tmp_path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    with pytest.raises(ingest.IngestError) as exc:
        ingest.fetch_local_file(pdf)
    # Either pypdf is installed (then this parses) or the message must be actionable.
    assert exc.value.code in ("PDF_SUPPORT_MISSING", "SOURCE_UNREADABLE")
    if exc.value.code == "PDF_SUPPORT_MISSING":
        assert "vault-memory[ingest]" in str(exc.value)


def test_extract_readable_drops_chrome_and_keeps_content():
    title, markdown = ingest.extract_readable(HTML)
    assert title == "Real Title"
    assert "First paragraph of the article." in markdown
    assert "# Heading One" in markdown
    assert "- Item one" in markdown
    assert "```" in markdown
    for chrome in ("Home | About", "Copyright nobody", "track()", ".x{}"):
        assert chrome not in markdown


def test_extract_readable_survives_malformed_html():
    title, markdown = ingest.extract_readable("<p>unclosed <b>bold <div>text")
    assert "text" in markdown
    assert title


def test_private_and_metadata_urls_are_refused(monkeypatch):
    """An API-key holder must not be able to aim the daemon at its own network."""
    monkeypatch.delenv(ingest.ALLOW_PRIVATE_URLS_ENV, raising=False)
    monkeypatch.delenv(ingest.ALLOWED_URL_HOSTS_ENV, raising=False)

    for url in (
        "http://localhost:5051/health",
        "http://127.0.0.1/admin",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.5/internal",
        "http://192.168.1.1/router",
        "http://[::1]:8080/",
        "http://metadata.google.internal/computeMetadata/v1/",
        "http://vault.internal/secrets",
    ):
        with pytest.raises(ingest.IngestError) as exc:
            asyncio.run(ingest.fetch_url(url, client=_Client()))
        assert exc.value.code == "URL_NOT_ALLOWED", url


def test_url_allowlist_is_strict_when_set(monkeypatch):
    monkeypatch.delenv(ingest.ALLOW_PRIVATE_URLS_ENV, raising=False)
    monkeypatch.setenv(ingest.ALLOWED_URL_HOSTS_ENV, "wiki.example.com, docs.example.com")

    client = _Client()
    source = asyncio.run(ingest.fetch_url("https://docs.example.com/page", client=client))
    assert client.requested == ["https://docs.example.com/page"]
    assert source.kind == "url"

    # Anything else is refused even though it is a perfectly public host.
    for url in ("https://example.com/", "http://localhost:5051/", "https://169.254.169.254/"):
        with pytest.raises(ingest.IngestError) as exc:
            asyncio.run(ingest.fetch_url(url, client=_Client()))
        assert exc.value.code == "URL_NOT_ALLOWED", url


def test_private_urls_can_be_opted_into(monkeypatch):
    monkeypatch.setenv(ingest.ALLOW_PRIVATE_URLS_ENV, "1")
    client = _Client()
    source = asyncio.run(ingest.fetch_url("http://localhost:8080/wiki", client=client))
    assert client.requested == ["http://localhost:8080/wiki"]
    assert source.kind == "url"


def test_fetch_url_uses_the_injected_client():
    client = _Client()
    source = asyncio.run(ingest.fetch_url("https://example.com/post", client=client))
    assert client.requested == ["https://example.com/post"]
    assert source.kind == "url"
    assert source.title == "Real Title"
    assert source.ref == "https://example.com/post"


def test_fetch_url_reports_unreachable_and_empty():
    with pytest.raises(ingest.IngestError) as exc:
        asyncio.run(ingest.fetch_url("https://example.com", client=_Client(status=500)))
    assert exc.value.code == "SOURCE_UNREACHABLE"

    with pytest.raises(ingest.IngestError) as exc:
        asyncio.run(ingest.fetch_url("https://example.com", client=_Client(text="   ")))
    assert exc.value.code == "SOURCE_EMPTY"

    with pytest.raises(ingest.IngestError) as exc:
        asyncio.run(ingest.fetch_url("not-a-url"))
    assert exc.value.code == "INVALID_SOURCE"


def test_fetch_url_keeps_raw_text_for_non_html_responses():
    client = _Client(text="# Plain markdown\n\nno html here")
    source = asyncio.run(ingest.fetch_url("https://example.com/raw.md", client=client))
    assert "no html here" in source.content


def test_redirects_are_revalidated_at_every_hop(monkeypatch):
    """A public URL that 302s into the metadata service must not be followed.

    ``follow_redirects=True`` would issue the second request without the guard
    ever seeing it, which is a complete bypass of the SSRF policy.
    """
    monkeypatch.delenv(ingest.ALLOW_PRIVATE_URLS_ENV, raising=False)
    monkeypatch.delenv(ingest.ALLOWED_URL_HOSTS_ENV, raising=False)

    client = _Client(
        redirects={
            "https://public.example/doc": (302, "http://169.254.169.254/latest/meta-data/"),
            "https://public.example/rel": (302, "/"),
            "https://docs.example.com/ok": (302, "/article"),
        }
    )
    with pytest.raises(ingest.IngestError) as exc:
        asyncio.run(ingest.fetch_url("https://public.example/doc", client=client))
    assert exc.value.code == "URL_NOT_ALLOWED"
    # The forbidden hop was refused before it was ever requested.
    assert client.requested == ["https://public.example/doc"]

    # A relative Location is resolved against the hop that was just validated.
    source = asyncio.run(ingest.fetch_url("https://docs.example.com/ok", client=client))
    assert client.requested[-2:] == ["https://docs.example.com/ok", "https://docs.example.com/article"]
    assert source.ref == "https://docs.example.com/ok"


def test_redirect_loop_and_missing_location_are_refused(monkeypatch):
    monkeypatch.setenv(ingest.ALLOW_PRIVATE_URLS_ENV, "1")
    loop = _Client(
        redirects={
            "http://wiki.local/a": (301, "http://wiki.local/b"),
            "http://wiki.local/b": (301, "http://wiki.local/a"),
        }
    )
    with pytest.raises(ingest.IngestError) as exc:
        asyncio.run(ingest.fetch_url("http://wiki.local/a", client=loop))
    assert exc.value.code == "SOURCE_UNREACHABLE"
    # MAX_REDIRECTS + the initial request.
    assert len(loop.requested) == ingest.MAX_REDIRECTS + 1

    bare = _Client(redirects={"http://wiki.local/gone": (302, "")})
    with pytest.raises(ingest.IngestError) as exc:
        asyncio.run(ingest.fetch_url("http://wiki.local/gone", client=bare))
    assert exc.value.code == "SOURCE_UNREACHABLE"


def test_fetch_source_dispatches_text_file_and_url(tmp_path):
    pasted = asyncio.run(ingest.fetch_source("", text="raw pasted thinking"))
    assert pasted.kind == "text" and pasted.ref == "pasted"

    path = tmp_path / "note.txt"
    path.write_text("file body", encoding="utf-8")
    from_file = asyncio.run(ingest.fetch_source("note.txt", vault_root=tmp_path))
    assert from_file.kind == "file" and from_file.content == "file body"

    from_url = asyncio.run(ingest.fetch_source("https://example.com", client=_Client()))
    assert from_url.kind == "url"

    with pytest.raises(ingest.IngestError):
        asyncio.run(ingest.fetch_source("", text="   "))


def test_local_reads_are_confined_to_the_vault(tmp_path):
    """Defense in depth: the pipeline refuses an out-of-vault path itself."""
    vault = tmp_path / "vault"
    vault.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("secret", encoding="utf-8")

    with pytest.raises(ingest.IngestError) as exc:
        ingest.resolve_local_source(str(outside), vault)
    assert exc.value.code == "UNAUTHORIZED_SOURCE"

    with pytest.raises(ingest.IngestError) as exc:
        ingest.resolve_local_source("../outside.md", vault)
    assert exc.value.code == "UNAUTHORIZED_SOURCE"

    inside = vault / "ok.md"
    inside.write_text("fine", encoding="utf-8")
    assert ingest.resolve_local_source("ok.md", vault) == inside.resolve()

    nested = vault / "inbox" / "deep.md"
    nested.parent.mkdir()
    nested.write_text("fine", encoding="utf-8")
    assert ingest.resolve_local_source("inbox/deep.md", vault) == nested.resolve()

    # Every component must be its own basename, so no absolute path and no
    # traversal segment ever reaches the filesystem.
    for bad in (str(inside), "sub/../ok.md", "../ok.md", "/etc/passwd"):
        with pytest.raises(ingest.IngestError) as exc:
            ingest.resolve_local_source(bad, vault)
        assert exc.value.code == "UNAUTHORIZED_SOURCE", bad


def test_symlinks_out_of_the_vault_are_refused(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("secret", encoding="utf-8")
    link = vault / "link.md"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform")

    with pytest.raises(ingest.IngestError) as exc:
        ingest.resolve_local_source("link.md", vault)
    assert exc.value.code == "UNAUTHORIZED_SOURCE"


def test_fetch_source_requires_a_vault_root_for_local_paths(tmp_path):
    path = tmp_path / "note.md"
    path.write_text("body", encoding="utf-8")
    with pytest.raises(ingest.IngestError) as exc:
        asyncio.run(ingest.fetch_source("note.md"))
    assert exc.value.code == "INVALID_SOURCE"


# ---------------------------------------------------------------------------
# archive + manifest
# ---------------------------------------------------------------------------

def test_archive_is_immutable_and_records_provenance(tmp_path):
    source = ingest.Source(kind="url", ref="https://example.com/a", title="My Source", content="body one")

    first = ingest.archive_source(tmp_path, source)
    assert first["created"] is True
    assert first["path"].startswith("raw/") and first["path"].endswith("-my-source.md")

    text = Path(first["raw_path"]).read_text(encoding="utf-8")
    assert "source-kind: url" in text
    assert "source-ref: https://example.com/a" in text
    assert "source-hash:" in text
    assert "body one" in text

    # Same bytes: reuse the archive rather than rewriting it.
    again = ingest.archive_source(tmp_path, source)
    assert again["path"] == first["path"]
    assert again["created"] is False

    # Different bytes at the same title: a new file, never an overwrite.
    changed = ingest.Source(kind="url", ref="https://example.com/a", title="My Source", content="body two")
    third = ingest.archive_source(tmp_path, changed)
    assert third["created"] is True
    assert third["path"] != first["path"]
    assert Path(first["raw_path"]).read_text(encoding="utf-8").count("body one") == 1


def test_manifest_round_trip_and_corrupt_manifest_degrades(tmp_path):
    assert ingest.read_manifest(tmp_path) == {"sources": {}}

    ingest.write_manifest(tmp_path, {"sources": {"a": {"content_hash": "h", "compiled_at": "t"}}})
    assert ingest.read_manifest(tmp_path)["sources"]["a"]["compiled_at"] == "t"

    ingest.manifest_path(tmp_path).write_text("{not json", encoding="utf-8")
    assert ingest.read_manifest(tmp_path) == {"sources": {}}

    ingest.manifest_path(tmp_path).write_text('["wrong shape"]', encoding="utf-8")
    assert ingest.read_manifest(tmp_path) == {"sources": {}}


def test_is_processed_requires_a_match_and_a_compile(tmp_path):
    source = ingest.Source(kind="file", ref="/x.md", title="X", content="body")
    assert ingest.is_processed(tmp_path, source) is False

    ingest.write_manifest(
        tmp_path,
        {"sources": {"/x.md": {"content_hash": source.content_hash, "compiled_at": "2026-09-12"}}},
    )
    assert ingest.is_processed(tmp_path, source) is True

    ingest.write_manifest(tmp_path, {"sources": {"/x.md": {"content_hash": "other", "compiled_at": "x"}}})
    assert ingest.is_processed(tmp_path, source) is False

    # Archived but never compiled stays pending.
    ingest.write_manifest(tmp_path, {"sources": {"/x.md": {"content_hash": source.content_hash, "compiled_at": None}}})
    assert ingest.is_processed(tmp_path, source) is False


# ---------------------------------------------------------------------------
# compile response parsing and claim tagging
# ---------------------------------------------------------------------------

def test_parse_compile_response_is_tolerant():
    parsed = ingest.parse_compile_response(json.dumps(COMPILE_PAYLOAD))
    assert parsed["pages"][0]["title"] == "Ingestion Inbox"
    assert len(parsed["triples"]) == 1

    # Prose-wrapped and code-fenced responses still parse.
    fenced = "Here you go:\n```json\n" + json.dumps(COMPILE_PAYLOAD) + "\n```"
    assert ingest.parse_compile_response(fenced)["pages"][0]["title"] == "Ingestion Inbox"

    for junk in (None, "", "nope", "[]", "42"):
        assert ingest.parse_compile_response(junk) == {"summary": "", "pages": [], "triples": []}


def test_parse_compile_response_normalises_claim_tags_and_drops_incomplete():
    parsed = ingest.parse_compile_response(
        json.dumps(
            {
                "pages": [
                    {"title": "T", "body": "B", "claims": [{"text": "c", "tag": "MADE_UP"}]},
                    {"title": "", "body": "dropped"},
                    {"title": "no body", "body": "   "},
                ]
            }
        )
    )
    assert [p["title"] for p in parsed["pages"]] == ["T"]
    assert parsed["pages"][0]["claims"][0]["tag"] == "inferred"


def test_claim_counts_and_rendered_section():
    pages = ingest.parse_compile_response(json.dumps(COMPILE_PAYLOAD))["pages"]
    counts = ingest.claim_tag_counts(pages)
    assert counts == {"extracted": 1, "inferred": 1, "ambiguous": 1}

    section = ingest.render_claims_section(pages[0]["claims"])
    assert "| extracted |" in section
    assert "| ambiguous |" in section
    assert ingest.render_claims_section([]) == ""


# ---------------------------------------------------------------------------
# compile pass
# ---------------------------------------------------------------------------

def test_compile_creates_a_page_with_provenance(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "daemon.routes.knowledge._persist_cognify_triples",
        lambda triples, deps: {"relationships_written": len(triples)},
    )
    source = ingest.Source(kind="url", ref="https://example.com", title="Src", content="raw body")
    archived = ingest.archive_source(tmp_path, source)

    result = asyncio.run(
        ingest.compile_source(_deps(tmp_path), tmp_path, source, archived=archived, llm=_llm(COMPILE_PAYLOAD))
    )

    assert result["status"] == "compiled"
    assert result["pages_created"] == ["Knowledge/concept-Ingestion-Inbox.md"]
    assert result["relationships_written"] == 1
    assert result["claim_tags"] == {"extracted": 1, "inferred": 1, "ambiguous": 1}

    page = (tmp_path / result["pages_created"][0]).read_text(encoding="utf-8")
    assert "trust: medium" in page
    assert "decay-profile: reference" in page
    assert "source-raw: raw/" in page
    assert "claim-tags: extracted=1,inferred=1,ambiguous=1" in page
    assert "[[vault-memory]]" in page
    assert "| extracted | Sources are archived under raw/ |" in page


def test_compile_never_overwrites_a_high_trust_page(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "daemon.routes.knowledge._persist_cognify_triples",
        lambda triples, deps: {"relationships_written": 0},
    )
    knowledge = tmp_path / "Knowledge"
    knowledge.mkdir()
    target = knowledge / "vault-memory.md"
    target.write_text("---\ntitle: vault-memory\ntrust: high\n---\nProtect me.\n", encoding="utf-8")

    payload = {
        "pages": [
            {"title": "vault-memory", "page_type": "entity", "body": "rewritten", "claims": [], "entities": []}
        ]
    }
    source = ingest.Source(kind="text", ref="pasted", title="Src", content="body")
    result = asyncio.run(
        ingest.compile_source(
            _deps(tmp_path), tmp_path, source, archived=ingest.archive_source(tmp_path, source), llm=_llm(payload)
        )
    )

    assert result["conflicts"] == [
        {"title": "vault-memory", "path": "Knowledge/vault-memory.md", "reason": "high-trust page"}
    ]
    assert result["pages_updated"] == []
    assert "Protect me." in target.read_text(encoding="utf-8")
    assert "rewritten" not in target.read_text(encoding="utf-8")


def test_compile_appends_to_an_existing_low_trust_page(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "daemon.routes.knowledge._persist_cognify_triples",
        lambda triples, deps: {"relationships_written": 0},
    )
    knowledge = tmp_path / "Knowledge"
    knowledge.mkdir()
    target = knowledge / "concept-Ingestion-Inbox.md"
    target.write_text("---\ntitle: Ingestion Inbox\ntrust: medium\n---\nOriginal.\n", encoding="utf-8")

    source = ingest.Source(kind="text", ref="pasted", title="Src", content="body")
    result = asyncio.run(
        ingest.compile_source(
            _deps(tmp_path), tmp_path, source, archived=ingest.archive_source(tmp_path, source), llm=_llm(COMPILE_PAYLOAD)
        )
    )

    assert result["pages_updated"] == ["Knowledge/concept-Ingestion-Inbox.md"]
    text = target.read_text(encoding="utf-8")
    assert "Original." in text
    assert "## Update —" in text
    assert "The inbox feeds [[vault-memory]]." in text


def test_triple_persistence_failure_does_not_lose_the_pages(tmp_path, monkeypatch):
    def _boom(triples, deps):
        raise RuntimeError("graph down")

    monkeypatch.setattr("daemon.routes.knowledge._persist_cognify_triples", _boom)
    source = ingest.Source(kind="text", ref="pasted", title="Src", content="body")

    result = asyncio.run(
        ingest.compile_source(
            _deps(tmp_path), tmp_path, source, archived=ingest.archive_source(tmp_path, source), llm=_llm(COMPILE_PAYLOAD)
        )
    )
    assert result["status"] == "compiled"
    assert result["pages_created"]
    assert result["relationships_written"] == 0


def test_compile_reports_failure_without_raising(tmp_path):
    async def _explode(prompt, model=None):
        raise RuntimeError("ollama down")

    source = ingest.Source(kind="text", ref="pasted", title="Src", content="body")
    result = asyncio.run(
        ingest.compile_source(_deps(tmp_path), tmp_path, source, llm=_explode)
    )
    assert result["status"] == "failed"
    assert "ollama down" in result["error"]


# ---------------------------------------------------------------------------
# end to end (unit level)
# ---------------------------------------------------------------------------

def test_ingest_archives_compiles_and_records_the_delta(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "daemon.routes.knowledge._persist_cognify_triples",
        lambda triples, deps: {"relationships_written": len(triples)},
    )
    doc = tmp_path / "source.md"
    doc.write_text("# Source Doc\n\nReal content.\n", encoding="utf-8")

    first = asyncio.run(ingest.ingest(_deps(tmp_path), tmp_path, "source.md", llm=_llm(COMPILE_PAYLOAD)))
    assert first["status"] == "compiled"
    assert Path(tmp_path / first["raw_path"]).exists()
    assert (tmp_path / "Knowledge" / "concept-Ingestion-Inbox.md").exists()
    assert ingest.read_manifest(tmp_path)["sources"][str(doc.resolve())]["compiled_at"]

    # Delta: unchanged content is skipped rather than recompiled.
    second = asyncio.run(ingest.ingest(_deps(tmp_path), tmp_path, "source.md", llm=_llm(COMPILE_PAYLOAD)))
    assert second["status"] == "skipped"
    assert second["reason"] == "unchanged"

    # force overrides the delta check.
    third = asyncio.run(ingest.ingest(_deps(tmp_path), tmp_path, "source.md", llm=_llm(COMPILE_PAYLOAD), force=True))
    assert third["status"] == "compiled"


def test_ingest_reports_a_bad_source_instead_of_raising(tmp_path):
    result = asyncio.run(ingest.ingest(_deps(tmp_path), tmp_path, "missing.md"))
    assert result["status"] == "failed"
    assert result["code"] == "SOURCE_NOT_FOUND"

    escaped = asyncio.run(ingest.ingest(_deps(tmp_path), tmp_path, "../outside.md"))
    assert escaped["status"] == "failed"
    assert escaped["code"] == "UNAUTHORIZED_SOURCE"


def test_ingest_inbox_drains_and_optionally_clears(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "daemon.routes.knowledge._persist_cognify_triples",
        lambda triples, deps: {"relationships_written": 0},
    )
    inbox = ingest.ensure_inbox(tmp_path)
    (inbox / "a.md").write_text("# A\n\ncontent a\n", encoding="utf-8")
    (inbox / "b.txt").write_text("content b\n", encoding="utf-8")

    summary = asyncio.run(
        ingest.ingest_inbox(_deps(tmp_path), tmp_path, llm=_llm(COMPILE_PAYLOAD), remove_after=True)
    )
    assert summary["queued"] == 2
    assert summary["compiled"] == 2
    assert not (inbox / "a.md").exists()
    assert len(ingest.read_manifest(tmp_path)["sources"]) == 2


def test_ensure_inbox_creates_a_self_documenting_drop_point(tmp_path):
    inbox = ingest.ensure_inbox(tmp_path)
    assert inbox.is_dir()
    assert "vault-memory ingest" in (inbox / "README.md").read_text(encoding="utf-8")
    # Idempotent: a human's own README is not clobbered.
    (inbox / "README.md").write_text("mine", encoding="utf-8")
    ingest.ensure_inbox(tmp_path)
    assert (inbox / "README.md").read_text(encoding="utf-8") == "mine"


# ---------------------------------------------------------------------------
# request model
# ---------------------------------------------------------------------------

def test_ingest_request_requires_exactly_one_source():
    from daemon.models.ingest import IngestRequest

    assert IngestRequest(text="hi").text == "hi"
    assert IngestRequest(url="https://x.dev").url == "https://x.dev"
    for bad in ({}, {"path": "a.md", "url": "https://x.dev"}, {"text": "a", "url": "https://x.dev"}):
        with pytest.raises(ValidationError):
            IngestRequest(**bad)


def test_ingest_request_rejects_traversal():
    from daemon.models.ingest import IngestRequest

    with pytest.raises(ValidationError):
        IngestRequest(path="../../etc/passwd")
    with pytest.raises(ValidationError):
        IngestRequest(path="docs/../../../etc/passwd")


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------

def test_ingest_route_refuses_a_path_outside_the_vault(tmp_path, monkeypatch):
    from daemon.models.ingest import IngestRequest
    from daemon.routes.ingest import ingest_source

    outside = tmp_path.parent / "outside.md"
    outside.write_text("secret", encoding="utf-8")
    vault = tmp_path / "vault"
    vault.mkdir()

    deps = SimpleNamespace(settings=SimpleNamespace(lite_mode=False, vault_path=str(vault)))
    res = asyncio.run(
        ingest_source(IngestRequest(path=str(outside)), deps=deps, _auth="ok")
    )
    assert res.status_code == 400
    assert not (vault / "raw").exists()


def test_ingest_route_accepts_a_vault_relative_path(tmp_path, monkeypatch):
    from daemon.models.ingest import IngestRequest
    from daemon.routes.ingest import ingest_source

    monkeypatch.setattr(
        "daemon.routes.knowledge._persist_cognify_triples",
        lambda triples, deps: {"relationships_written": 0},
    )
    # Stub the provider dispatch so the route never reaches a real Ollama.
    monkeypatch.setattr("daemon.miner.make_default_llm", lambda settings=None: _llm(COMPILE_PAYLOAD))

    vault = tmp_path / "vault"
    (vault / "inbox").mkdir(parents=True)
    (vault / "inbox" / "doc.md").write_text("# Doc\n\nbody\n", encoding="utf-8")

    deps = SimpleNamespace(
        settings=SimpleNamespace(lite_mode=False, vault_path=str(vault)),
        watcher=None,
    )
    res = asyncio.run(
        ingest_source(
            IngestRequest(path="inbox/doc.md"),
            deps=deps,
            _auth="ok",
        )
    )
    assert res["status"] == "compiled"


def test_manifest_route_lists_sources_and_pending_inbox(tmp_path):
    from daemon.routes.ingest import ingest_manifest

    vault = tmp_path / "vault"
    ingest.ensure_inbox(vault)
    (vault / "inbox" / "pending.md").write_text("x", encoding="utf-8")

    deps = SimpleNamespace(settings=SimpleNamespace(lite_mode=False, vault_path=str(vault)))
    res = asyncio.run(ingest_manifest(deps=deps, _auth="ok"))
    assert res["count"] == 0
    # The inbox README documents the drop point; it is not a source.
    assert [p["file"] for p in res["inbox_pending"]] == ["inbox/pending.md"]


# ---------------------------------------------------------------------------
# lint
# ---------------------------------------------------------------------------

def test_lint_flags_a_page_that_drifted_into_speculation(tmp_path):
    from daemon.lint import _find_speculative_pages

    knowledge = tmp_path / "Knowledge"
    knowledge.mkdir()
    (knowledge / "speculative.md").write_text(
        "---\ntitle: Speculative\nclaim-tags: extracted=1,inferred=0,ambiguous=3\n---\nbody\n",
        encoding="utf-8",
    )
    (knowledge / "solid.md").write_text(
        "---\ntitle: Solid\nclaim-tags: extracted=5,inferred=1,ambiguous=0\n---\nbody\n",
        encoding="utf-8",
    )
    (knowledge / "handwritten.md").write_text("---\ntitle: Hand written\n---\nbody\n", encoding="utf-8")

    flagged = _find_speculative_pages(MagicMock(), tmp_path)
    assert [f["vault_path"] for f in flagged] == ["Knowledge/speculative.md"]
    assert flagged[0]["ambiguous_ratio"] == 0.75
    assert flagged[0]["total_claims"] == 4


def test_mcp_ingest_tool_is_registered_and_dispatches(monkeypatch):
    import httpx

    import cli.mcp_adapter as adapter
    import cli.mcp_client as client

    assert "memory/ingest" in {t["name"] for t in adapter.TOOLS}

    seen = {}

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"status": "compiled"}

    def _post(url, **kwargs):
        seen["url"] = url
        seen["json"] = kwargs.get("json")
        return _Resp()

    monkeypatch.setattr(httpx, "post", _post)
    result = client.call_daemon("http://d", "memory/ingest", {"path": "inbox/a.md"})
    assert result == {"status": "compiled"}
    assert seen["url"] == "http://d/ingest"
    assert seen["json"] == {"path": "inbox/a.md", "force": False}

    # No source given: the tool reports rather than sending an empty payload.
    assert "error" in client.call_daemon("http://d", "memory/ingest", {})
