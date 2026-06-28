"""
Synapse graph package — F4 knowledge graph (v0.3, M3).

Public exports:
  GraphEngine  — 4-signal edge-weight computation + seeded FA2 layout (ADR-0012, ADR-0013)
  GraphCache   — dataVersion-debounced in-process cache (ADR-0014)

I2 guarantee: FA2 runs ONLY in engine.py (server-side, igraph R9). Coordinates are
persisted in pages.x/y (ADR-0013). GET /graph returns precomputed coords. No layout
code exists or may be introduced in frontend/ (ADR-0015).
"""

from app.graph.cache import GraphCache
from app.graph.engine import GraphEngine

__all__ = ["GraphEngine", "GraphCache"]
