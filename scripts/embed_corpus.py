"""Embed every chunk lacking a vector, then build the HNSW index. Resumable."""
import time

from filing_agent.retrieval import db
from filing_agent.retrieval.embed import (
    assert_embeddings_complete,
    create_hnsw_index,
    embed_pending,
    load_encoder,
    pending_count,
    resolve_device,
)

conn = db.connect()
todo = pending_count(conn)
print(f"device={resolve_device()}  chunks pending={todo:,}", flush=True)
encoder = load_encoder()
t0 = time.time()
done = embed_pending(conn, encoder, batch_size=64)
print(f"embedded {done:,} chunks in {time.time()-t0:.0f}s", flush=True)
assert_embeddings_complete(conn)
print("PASS assert_embeddings_complete", flush=True)
t1 = time.time()
create_hnsw_index(conn)
print(f"HNSW index built in {time.time()-t1:.0f}s", flush=True)
conn.close()
