import pytest
from daemon.sync_watcher import _chunk_text

def test_chunk_text_basic():
    text = "one two three four five six seven eight nine ten"
    # min_w is roughly 64 * 0.75 = 48 words.
    # CHUNK_SIZE_TOKENS is 512, so chunk_w is 512 * 0.75 = 384 words.
    # Our text is small, so it should return as one chunk.
    chunks = _chunk_text(text)
    assert chunks == [text]

def test_chunk_text_large():
    # Create text with 1000 words
    words = ["word" + str(i) for i in range(1000)]
    text = " ".join(words)
    chunks = _chunk_text(text)

    # Verify we have multiple chunks
    assert len(chunks) > 1

    # Verify all words are preserved (joined chunks should contain all words)
    # Note: chunks overlap, so simple join won't work, but we can check if all original words exist.
    all_chunks_text = " ".join(chunks)
    for word in words:
        assert word in all_chunks_text

def test_chunk_text_min_w_boundary():
    # chunk_w = 384, overlap_w = 57, min_w = 48
    # If we have 400 words:
    # Chunk 1: [0:384] -> 384 words. 384 >= 48, so it's kept.
    # i becomes 0 + 384 - 57 = 327
    # Chunk 2: [327:400] -> 73 words. 73 >= 48, so it's kept.
    # i becomes 327 + 384 - 57 = 654. Loop ends.
    words = ["w"] * 400
    text = " ".join(words)
    chunks = _chunk_text(text)
    assert len(chunks) == 2
    assert len(chunks[1].split()) == 73

def test_chunk_text_small_last_chunk():
    # If the last chunk is < min_w, it should be dropped (unless it's the first)
    # words = 384 + 20 = 404 words
    # Chunk 1: [0:384] -> kept
    # i = 327
    # Chunk 2: [327:404] -> 77 words. 77 >= 48, kept.

    # words = 384 + 10 = 394
    # Chunk 1: [0:384] -> kept
    # i = 327
    # Chunk 2: [327:394] -> 67 words. 67 >= 48, kept.

    # Let's try to get a small tail.
    # i starts at 0. end = 384. i becomes 327.
    # Next loop: end = 327 + 384 = 711.
    # If total words = 350.
    # Chunk 1: [0:350]. Kept. i becomes 384-57=327.
    # Next loop: i=327. end=350. Chunk [327:350] -> 23 words. 23 < 48. DROPPED.
    words = ["w"] * 350
    text = " ".join(words)
    chunks = _chunk_text(text)
    assert len(chunks) == 1
    assert len(chunks[0].split()) == 350
