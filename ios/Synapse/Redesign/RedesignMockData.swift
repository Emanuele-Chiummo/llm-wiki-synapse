import Foundation

/// Static, realistic mock content for the Fase A design-foundation screens
/// (ADR-0088). This is intentionally *not* API-backed — the point of Fase A is to
/// let the owner approve the visual direction before Fase B wires live data. All
/// copy is plausible Synapse content, never lorem ipsum.
enum RedesignMock {

    struct Page: Identifiable, Hashable {
        let id: String
        let title: String
        let type: String
        let summary: String
        var sources: Int = 0
        var links: Int = 0
    }

    struct DomainStat: Identifiable, Hashable {
        let id = UUID()
        let name: String
        let type: String
        let count: Int
    }

    struct ActivityItem: Identifiable, Hashable {
        let id = UUID()
        let icon: String
        let text: String
        let when: String
    }

    // Home ---------------------------------------------------------------------

    static let vaultName = "Homelab Knowledge"

    static let headline = (pages: 248, sources: 63, links: 914, review: 5)

    static let domains: [DomainStat] = [
        .init(name: "Concepts", type: "concept", count: 71),
        .init(name: "Entities", type: "entity", count: 58),
        .init(name: "Sources", type: "source", count: 63),
        .init(name: "Synthesis", type: "synthesis", count: 22),
        .init(name: "Comparisons", type: "comparison", count: 14),
        .init(name: "Queries", type: "query", count: 20),
    ]

    static let recent: [Page] = [
        .init(id: "p1", title: "TrueNAS SCALE dataset layout",
              type: "concept", summary: "How pools, datasets and snapshots map to the Synapse vault bind-mount.",
              sources: 4, links: 11),
        .init(id: "p2", title: "RTX 3060 — Ollama VRAM budget",
              type: "entity", summary: "12 GB envelope; which models fit alongside bge-m3 embeddings.",
              sources: 3, links: 7),
        .init(id: "p3", title: "Incremental indexing vs full re-scan",
              type: "comparison", summary: "Why Synapse updates only affected records (I1) instead of re-scanning.",
              sources: 6, links: 15),
        .init(id: "p4", title: "bge-m3 embedding pipeline",
              type: "source", summary: "Qdrant collection, dimensionality, and the ingest write path.",
              sources: 2, links: 9),
    ]

    static let activity: [ActivityItem] = [
        .init(icon: "arrow.down.doc.fill", text: "Ingested “SearXNG deep-research runbook”", when: "12m ago"),
        .init(icon: "sparkles", text: "Synthesised “Self-hosted RAG stack overview”", when: "1h ago"),
        .init(icon: "checkmark.seal.fill", text: "Approved 3 review suggestions", when: "3h ago"),
        .init(icon: "point.3.connected.trianglepath.dotted", text: "Graph layout recomputed (dataVersion 214)", when: "3h ago"),
    ]

    // Wiki ---------------------------------------------------------------------

    static let wikiPages: [Page] = [
        .init(id: "w1", title: "Adamic-Adar link weighting", type: "concept",
              summary: "The ×1.5 structural signal in the 4-signal relevance formula.", sources: 3, links: 8),
        .init(id: "w2", title: "Anthropic Messages API", type: "entity",
              summary: "Tool-use + JSON-schema backend behind ApiProvider (F17).", sources: 2, links: 6),
        .init(id: "w3", title: "claude-agent-sdk", type: "entity",
              summary: "The delegated CLI provider; runs its own agentic ingest loop.", sources: 4, links: 12),
        .init(id: "w4", title: "Deep Research loop", type: "concept",
              summary: "SearXNG multi-query → fetch → assess → refine → synthesise → auto-ingest.", sources: 5, links: 10),
        .init(id: "w5", title: "FA2 offline layout", type: "source",
              summary: "ForceAtlas2 runs server-side; coordinates persist in Postgres (I2).", sources: 2, links: 5),
        .init(id: "w6", title: "Local vs API vs CLI provider", type: "comparison",
              summary: "When to pick Ollama, Anthropic/OpenAI-compatible, or the bundled CLI.", sources: 6, links: 14),
        .init(id: "w7", title: "Which provider for a private vault?", type: "query",
              summary: "Routes to Local (Ollama) — offline, zero-cost, privacy-preserving.", sources: 1, links: 3),
        .init(id: "w8", title: "Self-hosted RAG stack overview", type: "synthesis",
              summary: "How Qdrant, bge-m3, Ollama and SearXNG compose the retrieval path.", sources: 7, links: 19),
    ]

    /// A short, realistic reading body for the Wiki detail mock (markdown-ish
    /// plain text; the real renderer arrives in Fase B).
    static let readingTitle = "Incremental indexing vs full re-scan"
    static let readingType = "comparison"
    static let readingBody = """
        Synapse never re-scans the whole vault. When a file changes, the watcher \
        updates only the affected records — metadata, links, the sources[] pivot, \
        and the graph coordinates — in Postgres, and the corresponding bge-m3 \
        vectors in Qdrant. This is invariant I1, and it is the first of the four \
        llm_wiki bottlenecks the project set out to fix.

        A full re-scan re-embeds every document on every change. On a large vault \
        that means minutes of GPU time and a graph that freezes while it recomputes. \
        The incremental path touches a handful of rows and debounces the layout \
        recompute on a dataVersion bump, so the UI stays fluid.

        The trade-off is bookkeeping: the watcher must track file identity across \
        renames and moves, and the link index must stay consistent when a page is \
        deleted (see cascade deletion). In practice that bookkeeping is far cheaper \
        than repeatedly re-reading a vault that mostly did not change.
        """
    static let readingSources = ["TrueNAS deployment notes", "llm_wiki bottleneck analysis", "Watcher design (ADR-0001)"]
    static let readingRelated = ["FA2 offline layout", "bge-m3 embedding pipeline", "Cascade deletion"]
}
