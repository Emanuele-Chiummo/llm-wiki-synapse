# Product naming review — 2026-07-13

## Recommendation

Keep **Synapse** as the display name for the current 1.6 line, qualify public copy as
**Synapse LLM Wiki**, and keep technical identifiers stable. In parallel, run formal clearance
on **Semaweave** as the preferred future master brand and **ScribeMesh** as the backup.

This is a product/engineering screening, not legal advice or trademark clearance.

## Why `Synapse` should not be the long-term unqualified brand

The name is already densely occupied in the same semantic territory:

- [Synapse Apps](https://www.synapseapps.io/) describes a connected, AI-readable second brain.
- [Synapse Knowledgebase](https://www.synapse-knowledgebase.com/) markets a self-hosted,
  AI-powered knowledge base.
- [usesynapse.ai](https://www.usesynapse.ai/) positions Synapse around organizational knowledge.
- [Synapse AI for Obsidian](https://community.obsidian.md/plugins/synapse-ai) turns a vault into a
  conversational knowledge base.
- [Synapse.org](https://docs.synapse.org/synapse-docs/creating-and-managing-wikis) already exposes
  a public wiki feature under the same name.

The issue is therefore discoverability and category confusion, not the quality of the current
mark. The supplied S-shaped neural/link mark remains usable for a future S-name.

## Shortlist

| Candidate | Position | Rationale | Current screening |
|---|---|---|---|
| **Semaweave** | Preferred future brand | Semantic knowledge woven into a connected wiki; preserves the S mark | No obvious exact product result in the initial web screen; full clearance still required |
| **ScribeMesh** | Backup | Documents/writing plus graph connectivity; category is understandable | No obvious exact product result in the initial web screen; full clearance still required |
| **Synapse LLM Wiki** | Transitional qualifier | Minimal migration and immediately explains the product | Still inherits the crowded Synapse namespace |

Rejected from the active shortlist:

- **Syntara**: active AI/software businesses already use the name
  ([syntara.site](https://www.syntara.site/), [syntara.es](https://syntara.es/)).
- **Signal Loom**: active AI companies, packages and applications already use it
  ([signal-loom.ai](https://signal-loom.ai/home),
  [PyPI](https://pypi.org/project/signalloom/)).

## Stable descriptor

Use the same descriptor under either the current or a future master brand:

> The self-hosted LLM wiki that turns your sources into connected knowledge.

Use **Connect everything.** as the secondary/emotional payoff, not as the only category statement.

## Rename architecture

The frontend now reads core display copy from `PRODUCT_IDENTITY`. A future rename should change
the display brand first while keeping these compatibility identifiers stable for at least one
major release:

- bundle identifier `ai.synapse.app`;
- `synapse.*` local-storage keys;
- database/Qdrant collection names;
- container, package and updater identifiers;
- MCP and skill names;
- environment variables.

Renaming compatibility identifiers in the same release would turn a marketing change into a data
migration. Treat each as an explicit, reversible migration with aliases and deprecation windows.

## Clearance checklist before approval

1. Search EUIPO, WIPO and relevant national trademark databases in software/SaaS classes.
2. Check exact and confusingly similar domains, GitHub organizations and repository names.
3. Check npm, PyPI, container registries, Homebrew/WinGet and browser extension stores.
4. Check Apple/Microsoft app stores and primary social handles.
5. Obtain legal review for the intended launch markets.
6. Only then update display brand, screenshots and store metadata; migrate technical identifiers
   in later, compatibility-preserving releases.
