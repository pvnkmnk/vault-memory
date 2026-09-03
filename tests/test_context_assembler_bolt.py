from daemon.context_assembler import _extract_headers, assemble_context


def test_extract_headers_basic():
    content = """# Header 1
Some paragraph text.
## Header 2
More text.
### Header 3
Final text.
"""
    expected = "# Header 1\n## Header 2\n### Header 3"
    assert _extract_headers(content) == expected


def test_extract_headers_no_headers():
    content = "Just plain text without any markdown headers."
    assert _extract_headers(content) == "(no headers)"


def test_extract_headers_ignores_hash_without_space():
    content = "#NotAHeader\n# Real Header\n##AnotherNonHeader"
    assert _extract_headers(content) == "# Real Header"


def test_extract_headers_h6_and_h7():
    content = "###### Level 6 Header\n####### Not Level 7 ATX Header"
    assert _extract_headers(content) == "###### Level 6 Header"
