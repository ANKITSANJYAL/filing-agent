"""Retrieval layer: Postgres FTS (lexical), pgvector (dense), RRF fusion, cross-encoder rerank.

This is the independent variable of the architecture ablation (proposal §4.2).
Arms B and C differ only in what happens inside this package.
"""
