"""Corpus construction: EDGAR HTML fetching, structure-aware chunking, XBRL fact loading.

Everything downstream (retrieval, agent, evals) reads what this package writes.
Ground truth for numeric verification originates here, not from the model.
"""
