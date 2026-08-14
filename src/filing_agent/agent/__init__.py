"""LangGraph agent: typed state, tool contracts, verification and re-plan loop.

Graph shape: planner -> retrieve -> xbrl_lookup -> calculator -> memo_writer,
with bounded re-plans (max 3) on low retrieval confidence or failed verification.
"""
