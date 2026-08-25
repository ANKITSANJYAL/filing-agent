"""Re-chunk, reload, re-embed and re-index after a chunking-parameter change."""
import time

from filing_agent.ingest.pipeline import build_all
from filing_agent.retrieval import db
from filing_agent.retrieval.embed import (
    assert_embeddings_complete,
    create_hnsw_index,
    embed_pending,
    load_encoder,
)

manifest, chunks, facts = build_all()
print(f"re-chunked: {len(chunks):,} chunks", flush=True)
conn = db.connect()
with conn.cursor() as cur:
    cur.execute("DELETE FROM chunks")          # chunk_ids change; a partial overwrite
conn.commit()                                  # would leave stale rows behind
db.init_schema(conn)
print("loaded:", db.load_chunks(conn, chunks), flush=True)
db.assert_loaded(conn, {"chunks": len(chunks)})

t0 = time.time()
done = embed_pending(conn, load_encoder(), batch_size=64)
print(f"embedded {done:,} in {time.time()-t0:.0f}s", flush=True)
assert_embeddings_complete(conn)
create_hnsw_index(conn)
print("PASS assert_embeddings_complete + HNSW rebuilt", flush=True)
conn.close()
