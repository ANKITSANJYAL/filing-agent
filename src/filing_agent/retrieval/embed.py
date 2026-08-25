"""BGE-M3 dense embeddings for the chunk table (PROPOSAL.md §8.2).

Local and free — no API dependency, so re-embedding the corpus during retrieval
experiments costs nothing but time. Vectors are L2-normalised, which makes pgvector's
cosine operator (`<=>`) equivalent to an inner product and keeps distances comparable
across chunks of different lengths.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Final

import psycopg

if TYPE_CHECKING:  # heavy import, only needed when actually encoding
    from sentence_transformers import SentenceTransformer

MODEL_NAME: Final[str] = "BAAI/bge-m3"
EMBEDDING_DIM: Final[int] = 1024
DEFAULT_BATCH: Final[int] = 32

# pgvector's HNSW build is memory-hungry; m/ef_construction here are the pgvector
# defaults, which are fine for a 9.5k-row table.
HNSW_M: Final[int] = 16
HNSW_EF_CONSTRUCTION: Final[int] = 64


class EmbeddingError(AssertionError):
    """Embedding state failed an expectation (D-0007)."""


def resolve_device(explicit: str | None = None) -> str:
    """Prefer Apple's Metal backend when present; fall back to CPU.

    CUDA is not checked for — this project's serving GPU work is T5 on rented Linux,
    and the local machine is Apple Silicon.
    """
    if explicit:
        return explicit
    if os.environ.get("EMBED_DEVICE"):
        return os.environ["EMBED_DEVICE"]
    try:
        import torch

        if torch.backends.mps.is_available():
            return "mps"
    except Exception:  # noqa: BLE001 - any probe failure just means CPU
        pass
    return "cpu"


def load_encoder(device: str | None = None, half: bool = True) -> SentenceTransformer:
    """Load BGE-M3, in fp16 by default on GPU backends.

    Measured on this corpus (Apple MPS, batch 64): fp32 8.2 chunks/s, fp16 9.9 — a 21%
    gain. Retrieval ranks by cosine similarity, so fp16's reduced mantissa is far below
    the margin that separates hits; the vectors are stored back as float32 regardless.

    Note `max_seq_length` is deliberately left alone: sentence-transformers pads each
    batch to its own longest item, not to the model ceiling, so lowering it would only
    truncate (99.5% of our chunks are under 1024 tokens) without buying speed.
    """
    from sentence_transformers import SentenceTransformer

    resolved = resolve_device(device)
    kwargs: dict[str, Any] = {}
    if half and resolved != "cpu":  # fp16 on CPU is slower, not faster
        import torch

        kwargs["model_kwargs"] = {"torch_dtype": torch.float16}
    return SentenceTransformer(MODEL_NAME, device=resolved, **kwargs)


def embed_texts(
    encoder: SentenceTransformer, texts: Sequence[str], batch_size: int = DEFAULT_BATCH
) -> Any:
    """Encode to L2-normalised vectors. Returns an (n, 1024) float32 array."""
    vectors = encoder.encode(
        list(texts),
        batch_size=batch_size,
        normalize_embeddings=True,
        show_progress_bar=False,
        convert_to_numpy=True,
    )
    if vectors.shape[1] != EMBEDDING_DIM:
        raise EmbeddingError(
            f"{MODEL_NAME} produced dim {vectors.shape[1]}, schema expects {EMBEDDING_DIM}"
        )
    return vectors


def pending_count(conn: psycopg.Connection) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM chunks WHERE embedding IS NULL")
        return cur.fetchone()[0]


def embed_pending(
    conn: psycopg.Connection,
    encoder: SentenceTransformer,
    batch_size: int = DEFAULT_BATCH,
    limit: int | None = None,
) -> int:
    """Embed chunks that have no vector yet, committing per batch.

    Resumable on purpose: encoding the corpus takes minutes, and a crash halfway
    through should cost the remaining work, not all of it.
    """
    done = 0
    while True:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT chunk_id, text FROM chunks WHERE embedding IS NULL"
                " ORDER BY chunk_id LIMIT %s",
                (batch_size,),
            )
            rows = cur.fetchall()
        if not rows:
            break
        vectors = embed_texts(encoder, [text for _, text in rows], batch_size)
        with conn.cursor() as cur:
            cur.executemany(
                "UPDATE chunks SET embedding = %s WHERE chunk_id = %s",
                [(vec, chunk_id) for (chunk_id, _), vec in zip(rows, vectors, strict=True)],
            )
        conn.commit()
        done += len(rows)
        if limit is not None and done >= limit:
            break
    return done


def create_hnsw_index(conn: psycopg.Connection) -> None:
    """Build the ANN index. Run *after* embedding — building it on an empty column is
    wasted work, and pgvector builds faster over populated data."""
    with conn.cursor() as cur:
        cur.execute(
            "CREATE INDEX IF NOT EXISTS chunks_embedding_idx ON chunks"
            " USING hnsw (embedding vector_cosine_ops)"
            f" WITH (m = {HNSW_M}, ef_construction = {HNSW_EF_CONSTRUCTION})"
        )
    conn.commit()


def assert_embeddings_complete(conn: psycopg.Connection, source: str = "<db>") -> None:
    """Every chunk must have a vector (D-0009).

    A partially embedded table still answers dense queries — it just silently cannot
    return the chunks that were skipped, which reads as poor recall rather than as a
    missing-data bug.
    """
    missing = pending_count(conn)
    if missing:
        raise EmbeddingError(f"{source}: {missing} chunk(s) have no embedding")
