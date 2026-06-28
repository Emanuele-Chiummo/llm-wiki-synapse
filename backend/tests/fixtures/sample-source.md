---
title: Introduction to Vector Databases
type: source
sources: []
lang: en
---

# Introduction to Vector Databases

Vector databases are a category of database management systems designed to store, manage, and
retrieve high-dimensional vectors — mathematical representations of data such as text, images,
or audio. Unlike traditional relational databases that store structured data in tables, vector
databases are optimised for similarity search: finding items whose embedding vectors are closest
to a query vector in high-dimensional space.

## Key Concepts

### Embeddings

An embedding is a dense numerical representation of a piece of data in a continuous vector
space. For text, embeddings are typically produced by language models such as bge-m3, where
semantically similar sentences have vectors with a small cosine distance.

### Approximate Nearest Neighbour (ANN) Search

Exact nearest-neighbour search is prohibitively slow at high dimensionality. Vector databases
use ANN algorithms (HNSW, IVF-PQ, etc.) to find approximate nearest neighbours in
sub-linear time, trading a small accuracy cost for large speed gains.

### Key Entities in Vector Database Systems

- **Collection**: a named set of vectors with an associated payload schema.
- **Point**: one vector together with its metadata payload.
- **Index**: the ANN data structure that accelerates search.
- **Score**: a similarity measure (cosine, dot-product, or Euclidean distance).

## Qdrant Overview

Qdrant is an open-source vector database written in Rust, designed for production use at scale.
It offers:

- Native support for HNSW indexing.
- A rich payload filtering system for hybrid search.
- An HTTP REST and gRPC API.
- On-disk persistence and cloud-native deployment options.

Qdrant is used by Synapse as its vector store, holding bge-m3 embeddings for every ingested
wiki page and enabling semantic search via the `search_wiki` MCP tool.

## bge-m3 Embedding Model

bge-m3 (BAAI General Embedding, multi-lingual, multi-functionality, multi-granularity) is a
state-of-the-art embedding model that produces 1024-dimensional dense vectors. It supports
over 100 languages and is already running on the TrueNAS RTX 3060 as part of the Synapse
infrastructure (I9 — no new service needed).

## Why Vector Databases Matter for Knowledge Graphs

In knowledge management systems like Synapse, vector databases complement graph databases:

- **Graph** encodes structural relationships ([[wikilinks]], entity co-occurrence, concept
  hierarchies).
- **Vector store** encodes semantic proximity — pages about related topics have similar vectors
  even without explicit links.

The combination enables the 4-phase retrieval pipeline (F5): tokenized keyword search,
graph-based expansion, budget-aware assembly, and citation-aware context building.

## References

- Qdrant documentation: https://qdrant.tech/documentation/
- bge-m3 model card: https://huggingface.co/BAAI/bge-m3
- HNSW paper: Malkov, Yu, and Yashunin (2018). "Efficient and robust ANN search via HNSW."
