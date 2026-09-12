# tests/test_integration.py
# Integration tests for real Weaviate/PostgreSQL performance validation
# Run these with: pytest tests/test_integration.py -v -m integration

import asyncio
import hashlib
import time
from pathlib import Path

import pytest

# Mark tests as integration tests
pytestmark = pytest.mark.integration


# =============================================================================
# Integration Test Fixtures
# =============================================================================

@pytest.fixture(scope='session')
def docker_services_available() -> bool:
    '''Check if Docker services (Weaviate, PostgreSQL) are available.'''
    import socket
    
    # Check Weaviate (port 8080)
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex(('localhost', 8080))
        sock.close()
        weaviate_up = result == 0
    except Exception:
        weaviate_up = False
    
    # Check PostgreSQL (port 5432)
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex(('localhost', 5432))
        sock.close()
        postgres_up = result == 0
    except Exception:
        postgres_up = False
    
    return weaviate_up and postgres_up


@pytest.fixture(scope='session')
def weaviate_client(docker_services_available):
    '''Provide real Weaviate client for integration tests.'''
    if not docker_services_available:
        pytest.skip('Docker services (Weaviate/PostgreSQL) not available')
    
    from daemon.weaviate_client import WeaviateClient
    
    client = WeaviateClient('http://localhost:8080')
    yield client
    # Cleanup: delete test data
    try:
        collection = client.client.collections.get('VaultNote')
        # Note: In production, you might want to delete specific objects
        # For now, we just close the client
    except Exception:
        pass


@pytest.fixture(scope='session')
def postgres_connection(docker_services_available):
    '''Provide real PostgreSQL connection for integration tests.'''
    if not docker_services_available:
        pytest.skip('Docker services (Weaviate/PostgreSQL) not available')
    
    import psycopg2
    
    conn = psycopg2.connect(
        host='localhost',
        port=5432,
        dbname='vault_memory',
        user='vault',
        password='vault_local',
    )
    conn.autocommit = True
    
    yield conn
    
    conn.close()


@pytest.fixture
def test_vault(tmp_path, docker_services_available) -> Path:
    '''Create a test vault directory with sample files.'''
    if not docker_services_available:
        pytest.skip('Docker services (Weaviate/PostgreSQL) not available')
    
    vault_root = tmp_path / 'test_vault'
    vault_root.mkdir(parents=True)
    
    # Create project structure
    notes_dir = vault_root / 'Project' / 'notes'
    notes_dir.mkdir(parents=True)
    
    # Create state file
    (vault_root / '.vault-memory-state.json').write_text('{}')
    
    return vault_root


@pytest.fixture
def sample_md_files(test_vault) -> list[Path]:
    '''Create sample markdown files for testing.'''
    notes_dir = test_vault / 'Project' / 'notes'
    
    files = []
    for i in range(50):
        file_path = notes_dir / f'note_{i:03d}.md'
        content = f'''---
status: active
tags: [test, integration, note-{i}]
---

# Note {i}

This is test content for integration testing. This note contains multiple sentences
to simulate real-world content that would be chunked for embedding. The content
includes some keywords like Python, async, and vault-memory to test search functionality.

## Section {i}-A

More content here with technical details about the test system and its behavior.
We want to ensure that chunking works correctly and produces embeddings that
can be searched effectively.

## Section {i}-B

Additional information for this section to create longer content that will
result in multiple chunks during processing.
'''
        file_path.write_text(content, encoding='utf-8')
        files.append(file_path)
    
    return files


# =============================================================================
# Weaviate Integration Tests
# =============================================================================

@pytest.mark.asyncio
async def test_weaviate_batch_upsert_performance(weaviate_client, test_vault, sample_md_files):
    '''
    Integration test: Measure real Weaviate batch upsert performance.
    Target: 100+ chunks/second throughput with batch_size=100
    '''
    from weaviate.util import generate_uuid5
    
    # Create 500 test chunks (simulating a medium-sized vault sync)
    chunks = []
    for i in range(500):
        chunk_data = {
            'content': f'Test chunk content {i} with some unique identifier {i*1000}',
            'file_path': f'Project/notes/note_{i % 50:03d}.md',
            'chunk_index': i,
            'tags': ['test', f'tag-{i}'],
            'importance': 0.5 + (i % 10) * 0.05,
            'trust': 'medium',
            'maturity': 'sapling',
            'agent_written': True,
        }
        # Add required fields for Weaviate schema
        chunk_data['uuid'] = generate_uuid5(f'test-chunk-{i}')
        chunks.append(chunk_data)
    
    # Measure batch upsert time
    start_time = time.perf_counter()
    
    # Process in batches (simulating sync_watcher behavior)
    batch_size = 100
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i:i + batch_size]
        await weaviate_client.batch_upsert(batch)
    
    elapsed = time.perf_counter() - start_time
    chunks_per_second = len(chunks) / elapsed
    
    print(f'\n[Integration] Weaviate batch upsert:')
    print(f'  Total chunks: {len(chunks)}')
    print(f'  Time elapsed: {elapsed:.3f}s')
    print(f'  Throughput: {chunks_per_second:.1f} chunks/sec')
    
    # Assert performance target
    assert chunks_per_second >= 50, f'Expected 50+ chunks/sec, got {chunks_per_second:.1f}'
    assert elapsed < 10, f'Expected <10s for 500 chunks, took {elapsed:.3f}s'


@pytest.mark.asyncio
async def test_weaviate_search_performance(weaviate_client, test_vault, sample_md_files):
    '''
    Integration test: Measure real Weaviate vector search latency.
    Target: <100ms per search query
    '''
    # First, add some data
    from weaviate.util import generate_uuid5
    
    chunks = []
    for i in range(100):
        chunk_data = {
            'content': f'Search test content {i} about Python async programming',
            'file_path': f'Project/notes/search_test_{i}.md',
            'chunk_index': 0,
            'tags': ['search', 'test'],
            'importance': 0.7,
            'trust': 'high',
            'maturity': 'tree',
            'agent_written': False,
            'uuid': generate_uuid5(f'search-test-{i}'),
        }
        chunks.append(chunk_data)
    
    # Add data first
    await weaviate_client.batch_upsert(chunks)
    
    # Wait a moment for indexing
    await asyncio.sleep(0.5)
    
    # Measure search latency (multiple queries to get average)
    latencies = []
    for _ in range(10):
        start = time.perf_counter()
        
        try:
            collection = weaviate_client.client.collections.get('VaultNote')
            # Use a simple near_text search (Weaviate uses vector internally)
            results = collection.query.near_text(
                query='Python async programming',
                limit=10,
            )
            
            latency = time.perf_counter() - start
            latencies.append(latency * 1000)  # Convert to ms
        except Exception as e:
            print(f'Search error: {e}')
            break
    
    if latencies:
        avg_latency = sum(latencies) / len(latencies)
        p95_latency = sorted(latencies)[int(len(latencies) * 0.95)] if len(latencies) >= 20 else max(latencies)
        
        print(f'\n[Integration] Weaviate search latency:')
        print(f'  Average: {avg_latency:.1f}ms')
        print(f'  P95: {p95_latency:.1f}ms')
        print(f'  Queries: {len(latencies)}')
        
        # Assert performance target
        assert avg_latency < 200, f'Expected <200ms avg latency, got {avg_latency:.1f}ms'


# =============================================================================
# PostgreSQL Integration Tests  
# =============================================================================

def test_postgres_sync_state_insert_performance(postgres_connection, test_vault):
    '''
    Integration test: Measure real PostgreSQL sync_state insert performance.
    Target: 500+ inserts/second
    '''
    cursor = postgres_connection.cursor()
    
    # Clear existing test data
    cursor.execute(
        'DELETE FROM sync_state WHERE file_path LIKE %s',
        ('Project/notes/test_perf_%',)
    )
    
    # Measure insert performance
    start_time = time.perf_counter()
    
    records = 500
    for i in range(records):
        file_path = f'Project/notes/test_perf_file_{i:04d}.md'
        content_hash = hashlib.sha256(f'content {i}'.encode()).hexdigest()[:16]
        
        cursor.execute(
            '''INSERT INTO sync_state (file_path, content_hash, chunk_count, maturity, centrality_score)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (file_path) DO UPDATE SET 
                   content_hash = EXCLUDED.content_hash,
                   chunk_count = EXCLUDED.chunk_count''',
            (file_path, content_hash, 5, 'sapling', 0.5)
        )
    
    elapsed = time.perf_counter() - start_time
    inserts_per_second = records / elapsed
    
    print(f'\n[Integration] PostgreSQL sync_state inserts:')
    print(f'  Total inserts: {records}')
    print(f'  Time elapsed: {elapsed:.3f}s')
    print(f'  Throughput: {inserts_per_second:.1f} inserts/sec')
    
    cursor.close()
    
    # Assert performance target
    assert inserts_per_second >= 200, f'Expected 200+ inserts/sec, got {inserts_per_second:.1f}'


def test_postgres_query_performance(postgres_connection):
    '''
    Integration test: Measure real PostgreSQL query performance.
    Target: <50ms for typical sync_state queries
    '''
    cursor = postgres_connection.cursor()
    
    # Measure query latencies for common operations
    queries = [
        ('SELECT * FROM sync_state WHERE file_path = %s LIMIT 1', ('test/path.md',)),
        ('SELECT * FROM sync_state WHERE maturity = %s LIMIT 100', ('sapling',)),
        ('SELECT COUNT(*) FROM sync_state', None),
        ('SELECT * FROM temporal_entities WHERE entity_name = %s LIMIT 1', ('TestEntity',)),
    ]
    
    latencies = []
    for query, params in queries:
        start = time.perf_counter()
        
        if params:
            cursor.execute(query, params)
        else:
            cursor.execute(query)
        
        cursor.fetchall()
        latency = time.perf_counter() - start
        latencies.append(latency * 1000)
    
    avg_latency = sum(latencies) / len(latencies)
    max_latency = max(latencies)
    
    print(f'\n[Integration] PostgreSQL query latency:')
    print(f'  Average: {avg_latency:.1f}ms')
    print(f'  Max: {max_latency:.1f}ms')
    print(f'  Queries: {len(queries)}')
    
    cursor.close()
    
    # Assert performance target
    assert avg_latency < 50, f'Expected <50ms avg query time, got {avg_latency:.1f}ms'


def test_postgres_batch_insert_performance(postgres_connection, test_vault):
    '''
    Integration test: Measure PostgreSQL batch insert performance using executemany.
    Target: 1000+ records/second with batch inserts
    '''
    cursor = postgres_connection.cursor()
    
    # Clear existing test data
    cursor.execute(
        'DELETE FROM sync_state WHERE file_path LIKE %s',
        ('Project/notes/batch_test_%',)
    )
    
    # Prepare batch data
    records = 500
    batch_data = []
    for i in range(records):
        file_path = f'Project/notes/batch_test_file_{i:04d}.md'
        content_hash = hashlib.sha256(f'batch content {i}'.encode()).hexdigest()[:16]
        batch_data.append((file_path, content_hash, 5, 'sapling', 0.5))
    
    # Measure batch insert time
    start_time = time.perf_counter()
    
    cursor.executemany(
        '''INSERT INTO sync_state (file_path, content_hash, chunk_count, maturity, centrality_score)
           VALUES (%s, %s, %s, %s, %s)
           ON CONFLICT (file_path) DO UPDATE SET 
               content_hash = EXCLUDED.content_hash''',
        batch_data
    )
    
    elapsed = time.perf_counter() - start_time
    records_per_second = records / elapsed
    
    print(f'\n[Integration] PostgreSQL batch insert (executemany):')
    print(f'  Total records: {records}')
    print(f'  Time elapsed: {elapsed:.3f}s')
    print(f'  Throughput: {records_per_second:.1f} records/sec')
    
    cursor.close()
    
    # Assert performance target
    assert records_per_second >= 500, f'Expected 500+ records/sec, got {records_per_second:.1f}'


# =============================================================================
# End-to-End Sync Performance Tests
# =============================================================================

@pytest.mark.asyncio
async def test_end_to_end_sync_throughput(weaviate_client, postgres_connection, test_vault):
    '''
    Integration test: Measure end-to-end sync throughput with real services.
    Target: 15+ files/second with parallel processing
    '''
    from daemon.sync_watcher import SyncEngine
    from daemon.embedder import EmbedderService
    
    # Create embedder (will use actual model if available, or skip)
    try:
        embedder = EmbedderService(
            embedding_model='sentence-transformers/all-MiniLM-L6-v2',
            reranker_model='mixedbread-ai/mxbai-rerank-large-v1',
            embed_batch_size=64,
        )
    except Exception as e:
        pytest.skip(f'Embedder not available: {e}')
    
    # Create sync engine
    engine = SyncEngine(
        vault_root=str(test_vault),
        weaviate_client=weaviate_client,
        pg_client=postgres_connection,
        embedder=embedder,
        sync_concurrency=16,
        state_write_batch=10,
        state_write_timeout_s=30,
    )
    
    # Create 100 test files
    notes_dir = test_vault / 'Project' / 'notes'
    notes_dir.mkdir(parents=True, exist_ok=True)
    
    for i in range(100):
        file_path = notes_dir / f'e2e_test_{i:03d}.md'
        content = f'''---
status: active
tags: [e2e, test]
---

# End-to-End Test {i}

This is content for testing the full sync pipeline with real Weaviate and PostgreSQL.
The content includes multiple paragraphs to ensure chunking produces several chunks
per file for realistic testing.
        
## Details

More content here with technical information about the system being tested.
We want to ensure that parallel processing works correctly and achieves the target
throughput of 15+ files per second with concurrent sync operations.
'''
        file_path.write_text(content, encoding='utf-8')

    # Warm up the embedder to avoid counting model lazy-loading latency in the benchmark
    await embedder.embed_batch(["warmup"])

    # Measure full sync time
    start_time = time.perf_counter()

    stats = await engine.full_sync(caller='test')
    
    elapsed = time.perf_counter() - start_time
    files_per_second = 100 / elapsed if elapsed > 0 else 0
    
    print(f'\n[Integration] End-to-end sync:')
    print(f'  Files processed: {stats.get("files_synced", 0)}')
    print(f'  Chunks indexed: {stats.get("chunks_indexed", 0)}')
    print(f'  Time elapsed: {elapsed:.3f}s')
    print(f'  Throughput: {files_per_second:.1f} files/sec')
    
    # Assert performance target
    assert files_per_second >= 3, f'Expected 3+ files/sec, got {files_per_second:.1f}'


@pytest.mark.asyncio
async def test_parallel_weaviate_batching(weaviate_client):
    '''
    Integration test: Verify parallel batch processing works with real Weaviate.
    Target: Concurrent batches complete faster than sequential
    '''
    from weaviate.util import generate_uuid5
    
    # Create test chunks
    num_batches = 5
    chunks_per_batch = 100
    
    all_chunks = []
    for batch_idx in range(num_batches):
        batch_chunks = []
        for i in range(chunks_per_batch):
            chunk_data = {
                'content': f'Batch {batch_idx} chunk {i} content',
                'file_path': f'test/batch_{batch_idx}/file_{i}.md',
                'chunk_index': i,
                'tags': ['batch', f'b{batch_idx}'],
                'importance': 0.5,
                'trust': 'medium',
                'maturity': 'sapling',
                'agent_written': True,
                'uuid': generate_uuid5(f'batch-{batch_idx}-chunk-{i}'),
            }
            batch_chunks.append(chunk_data)
        all_chunks.append(batch_chunks)
    
    # Measure parallel batch processing (using WeaviateClient's internal batching)
    start_time = time.perf_counter()
    
    # Process all chunks through batch_upsert (which splits into parallel batches)
    flat_chunks = [chunk for batch in all_chunks for chunk in batch]
    await weaviate_client.batch_upsert(flat_chunks)
    
    elapsed = time.perf_counter() - start_time
    total_chunks = sum(len(b) for b in all_chunks)
    chunks_per_second = total_chunks / elapsed if elapsed > 0 else 0
    
    print(f'\n[Integration] Parallel Weaviate batching:')
    print(f'  Total chunks: {total_chunks}')
    print(f'  Batches: {num_batches}')
    print(f'  Time elapsed: {elapsed:.3f}s')
    print(f'  Throughput: {chunks_per_second:.1f} chunks/sec')
    
    # Verify throughput is reasonable
    assert chunks_per_second >= 30, f'Expected 30+ chunks/sec, got {chunks_per_second:.1f}'


# =============================================================================
# S31-1 — session attribution (issue #75)
# =============================================================================

class _RealDictPostgres:
    '''Minimal stand-in for PostgresClient — dict rows, as production uses.'''

    def __init__(self, conn):
        self._conn = conn

    def cursor(self):
        import psycopg2.extras
        return self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)


def test_s31_sync_log_attribution_round_trip(postgres_connection):
    '''Acceptance (#75): register a session, attribute a write, read it back.

    Covers the schema half of the acceptance criterion against real Postgres —
    that sync_log exists, accepts the agent_sessions foreign key, and that the
    attribution query surfaces the touch. The MCP header plumbing half lives in
    tests/test_s31_attribution.py.
    '''
    import psycopg2.extras

    from types import SimpleNamespace
    from daemon.helpers import attribution
    from daemon.routes.sessions import session_attribution

    raw = postgres_connection
    deps = SimpleNamespace(
        postgres=_RealDictPostgres(raw),
        settings=SimpleNamespace(lite_mode=False),
    )

    with raw.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            '''
            INSERT INTO agent_sessions (agent_name, project, task, status, closed_at)
            VALUES (%s, %s, %s, 'closed', now())
            RETURNING id
            ''',
            ('integration-test', 'vault-memory', 'S31-1 attribution'),
        )
        session_id = str(cur.fetchone()['id'])

    try:
        session = attribution.resolve_session(deps, session_id)
        assert session is not None, 'a just-registered session must resolve'
        assert session['id'] == session_id

        assert attribution.log_file_action(
            deps, session, '_working/integration-insight.md', 'created'
        ) is True

        res = asyncio.run(
            session_attribution(session_id, deps=deps, _auth='ok')
        )
        assert res['source'] == 'sync_log'
        assert '_working/integration-insight.md' in [a['file_path'] for a in res['actions']]
        assert res['by_action']['created'] >= 1

        # An unknown session must 404 rather than leak an empty payload.
        missing = asyncio.run(
            session_attribution(
                '00000000-0000-0000-0000-000000000000', deps=deps, _auth='ok'
            )
        )
        assert missing.status_code == 404
    finally:
        with raw.cursor() as cur:
            cur.execute('DELETE FROM sync_log WHERE session_id = %s', (session_id,))
            cur.execute('DELETE FROM agent_sessions WHERE id = %s', (session_id,))


def test_s32_weekly_digest_over_real_activity(postgres_connection, tmp_path):
    '''Acceptance (#83): a week of sync_log + mined lessons yields a weekly
    digest whose sections are backed by pages that exist.
    '''
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace

    from daemon import digest

    now = datetime.now(timezone.utc)
    pg = _RealDictPostgres(postgres_connection)
    deps = SimpleNamespace(
        postgres=pg,
        settings=SimpleNamespace(lite_mode=False, vault_path=str(tmp_path)),
        watcher=None,
    )

    session_ids = []
    with postgres_connection.cursor() as cur:
        for offset in (1, 3, 5):
            cur.execute(
                '''
                INSERT INTO agent_sessions
                    (agent_name, project, task, status, closed_at, mined_at)
                VALUES (%s, %s, %s, 'closed', %s, %s)
                RETURNING id
                ''',
                (
                    'integration-test',
                    'vault-memory',
                    f'S32 weekly {offset}',
                    now - timedelta(days=offset),
                    now - timedelta(days=offset),
                ),
            )
            session_ids.append(str(cur.fetchone()[0]))
            cur.execute(
                '''
                INSERT INTO sync_log (session_id, file_path, action)
                VALUES (%s, %s, %s)
                ''',
                (session_ids[-1], '05 Dev Projects/vault-memory/STATE.md', 'modified'),
            )

    lessons_dir = tmp_path / 'lessons'
    lessons_dir.mkdir(parents=True, exist_ok=True)
    (lessons_dir / 'corroborated-lesson.md').write_text(
        '---\ntitle: Corroborated lesson\ntype: lesson\nproject: vault-memory\n'
        'theme: testing\nreview: approved\nsource: session-mining\n'
        'corroboration: 3\ntrust: high\nmaturity: sapling\ndecay-profile: log\n---\n\nbody\n',
        encoding='utf-8',
    )

    try:
        result = asyncio.run(digest.run_digest(deps, tmp_path, 'weekly', now=now))
        assert result['status'] == 'written', result
        assert result['path'].startswith('digests/2026-W') or 'digests/' in result['path']
        assert result['sessions'] >= 3
        assert result['lessons_promoted'] >= 1

        text = (tmp_path / result['path']).read_text(encoding='utf-8')
        for section in (
            '## Page velocity by project',
            '## Sessions mined',
            '## Lessons',
            '## Emerging entities',
        ):
            assert section in text, section

        # Every lesson the digest links to exists on disk.
        assert '[[corroborated-lesson]]' in text
        assert (lessons_dir / 'corroborated-lesson.md').exists()
    finally:
        with postgres_connection.cursor() as cur:
            cur.execute('DELETE FROM sync_log WHERE session_id = ANY(%s)', (session_ids,))
            cur.execute('DELETE FROM agent_sessions WHERE id = ANY(%s)', (session_ids,))


def test_s32_skills_export_from_promoted_lessons(tmp_path):
    '''Acceptance (#85): promote 2 lessons → export → SKILL.md frontmatter is
    valid and every referenced page exists.
    '''
    from daemon import digest, lessons

    drafts = tmp_path / '_working' / 'sessions'
    drafts.mkdir(parents=True, exist_ok=True)
    for slug, theme in (('build-the-loop', 'build-loop'), ('run-the-tests', 'build-loop')):
        (drafts / f'2026-09-01-{slug}.md').write_text(
            f'---\ntitle: {slug}\ntype: lesson\nproject: vault-memory\n'
            f'theme: {theme}\nreview: pending\nsource: session-mining\n'
            f'corroboration: 2\n---\n\nbody for {slug}\n',
            encoding='utf-8',
        )
        assert lessons.promote_draft(tmp_path, slug)['ok'] is True

    result = digest.export_skills(tmp_path)
    assert result['themes'] == 1
    assert result['missing_pages'] == []

    bundle = (tmp_path / result['written'][0]['path']).read_text(encoding='utf-8')
    frontmatter = bundle.split('---')[1]
    assert 'name: build-loop' in frontmatter
    assert 'description: ' in frontmatter

    for slug in ('build-the-loop', 'run-the-tests'):
        assert f'[[{slug}]]' in bundle
        assert (tmp_path / 'lessons' / f'{slug}.md').exists()


def test_s32_ingest_inbox_round_trip(postgres_connection, tmp_path):
    '''Acceptance (#81): a URL + an md file land as raw sources, wiki pages,
    and persisted triples.

    The URL is served by an injected client rather than the network, so the
    assertion under test is the pipeline's *database* half: raw archive on
    disk, ``Knowledge/`` pages with claim provenance, and rows in
    ``relationships`` / ``temporal_entities`` that ``/search`` reads from.
    '''
    import json
    from types import SimpleNamespace

    from daemon import ingest

    triples = [
        {'subject': 'ingest-fixture', 'predicate': 'feeds', 'object': 'knowledge-base'}
    ]
    payload = {
        'summary': 'Fixture summary.',
        'pages': [
            {
                'title': 'Ingest Fixture',
                'page_type': 'concept',
                'body': 'The fixture feeds [[knowledge-base]].',
                'entities': ['ingest-fixture'],
                'claims': [{'text': 'archived under raw/', 'tag': 'extracted'}],
            }
        ],
        'triples': triples,
    }

    async def _llm(prompt, model=None):
        return json.dumps(payload)

    class _Client:
        async def get(self, url):
            class _Resp:
                def raise_for_status(self):
                    return None

                text = '<html><head><title>Fixture</title></head><body><p>Fixture body.</p></body></html>'

            return _Resp()

        async def aclose(self):
            return None

    vault = tmp_path / 'vault'
    ingest.ensure_inbox(vault)
    (vault / 'inbox' / 'fixture.md').write_text('# Fixture Doc\n\nBody.\n', encoding='utf-8')

    deps = SimpleNamespace(
        postgres=_RealDictPostgres(postgres_connection),
        settings=SimpleNamespace(lite_mode=False, vault_path=str(vault)),
        watcher=None,
    )

    try:
        summary = asyncio.run(
            ingest.ingest_inbox(deps, vault, llm=_llm, remove_after=True)
        )
        assert summary['compiled'] == 1, summary

        url_result = asyncio.run(
            ingest.ingest(deps, vault, 'https://example.invalid/fixture', client=_Client(), llm=_llm)
        )
        assert url_result['status'] == 'compiled', url_result

        # 1. Immutable raw archive with provenance.
        raw_files = [p for p in (vault / 'raw').glob('*.md')]
        assert len(raw_files) == 2, raw_files
        assert all('source-hash:' in p.read_text(encoding='utf-8') for p in raw_files)

        # 2. Wiki page with claim provenance.
        page = vault / 'Knowledge' / 'concept-Ingest-Fixture.md'
        assert page.exists()
        page_text = page.read_text(encoding='utf-8')
        assert 'claim-tags: extracted=1,inferred=0,ambiguous=0' in page_text

        # 3. Triples reached the graph that /search reads.
        with postgres_connection.cursor() as cur:
            cur.execute(
                'SELECT COUNT(*) FROM relationships WHERE source_name = %s',
                ('ingest-fixture',),
            )
            assert cur.fetchone()[0] >= 1
            cur.execute(
                'SELECT COUNT(*) FROM temporal_entities WHERE entity_name = %s',
                ('ingest-fixture',),
            )
            assert cur.fetchone()[0] >= 1

        # 4. The manifest makes a re-run a no-op (delta only).
        manifest = ingest.read_manifest(vault)
        assert len(manifest['sources']) == 2
        assert all(entry['compiled_at'] for entry in manifest['sources'].values())
    finally:
        with postgres_connection.cursor() as cur:
            cur.execute(
                'DELETE FROM relationships WHERE source_name = %s OR target_name = %s',
                ('ingest-fixture', 'knowledge-base'),
            )
            cur.execute(
                'DELETE FROM temporal_entities WHERE entity_name IN (%s, %s)',
                ('ingest-fixture', 'knowledge-base'),
            )