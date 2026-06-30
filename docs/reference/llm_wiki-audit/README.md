# Analisi di riferimento — `nashsu/llm_wiki` v0.5.4

Audit tecnico approfondito del repo `nashsu/llm_wiki` (implementazione del pattern "LLM Wiki" di Karpathy), prodotto come **spec di riferimento** per il progetto parallelo *Synapse*.

Stack analizzato: Tauri v2 (Rust) + React 19/TS/Vite · Milkdown · sigma.js/graphology/ForceAtlas2 · LanceDB · Zustand · react-i18next. Versione: `0.5.4` (commit `c03c6be`).

## Documenti

| File | Contenuto |
|---|---|
| [`01-AUDIT-FUNZIONALE.md`](01-AUDIT-FUNZIONALE.md) | **FASE 1** — Claim README vs implementazione reale, feature-by-feature (16 macro-funzionalità), con `file:riga`. Cross-check col pattern Karpathy. |
| [`02-CODE-UI-REVIEW.md`](02-CODE-UI-REVIEW.md) | **FASE 2** — Code quality (Rust + TS), sicurezza, performance, test, UI/UX. Tabella per severità + effort di fix. |
| [`03-PIANO-AGENTICO-v1.0.md`](03-PIANO-AGENTICO-v1.0.md) | **FASE 3** — Piano a sprint v0.5→v1.0 per esecuzione agentica: task atomici, criteri di accettazione, struttura subagent, Definition of Done. |

## I 3 takeaway principali

1. **Non è vaporware.** ~95k LOC, 121 test TS + ~190 test Rust, type-safety quasi perfetta. 13/16 feature complete e testate.
2. **Il rischio nel riusare il README come spec sono i NUMERI** — diversi valori sono fattualmente errati vs codice (budget 50/5/15/30 non 60/20/5/15; nessun "+10"; grafo 1-hop senza decay; type-affinity premia il cross-type; score L2 non cosine; timeout 30 min; PDF via `pdfium-render`). **Le costanti autoritative sono nel codice.**
3. **Il debito è concentrato**, non diffuso: un clip server `:19827` insicuro (non autenticato), una CI che non esegue i test, e il layer UI non testato. Tutti fixabili in modo mirato, senza riprogetti.

> Metodo: clone locale + lettura diretta di README(EN/CN/JA/KO), `llm-wiki.md`, e dell'intero `src/`/`src-tauri/`/`extension/`/`mcp-server/`. Ogni verdetto è ancorato a riferimenti di codice.
