# LLM Wiki (`nashsu/llm_wiki`) — Audit funzionale: claim README vs implementazione reale

> **Oggetto**: `nashsu/llm_wiki` @ `v0.5.4` (commit `c03c6be`, tag `release: v0.5.4`).
> **Stack reale**: Tauri v2 (Rust) + React 19 / TypeScript / Vite · shadcn/ui + Tailwind v4 · Milkdown (editor) · sigma.js + graphology + ForceAtlas2 · LanceDB (vector, opzionale) · Zustand · react-i18next.
> **Metodo**: clone locale, lettura di README.md / README_CN.md / llm-wiki.md (pattern Karpathy) e analisi diretta di `src/`, `src-tauri/`, `extension/`, `mcp-server/`. Ogni verdetto è ancorato a `file:riga`.
> **Scopo**: spec di riferimento riusabile per il progetto parallelo *Synapse*. Tono tecnico, niente marketing.

---

## 0. Sintesi esecutiva (leggere prima di tutto)

**Questo NON è un progetto "v0.5 vaporware".** È una codebase matura: **~95.000 LOC**, **121 file di test** TypeScript (unit + property-based con `fast-check` + integration + scenario + 10 suite `real-llm`), **~190 funzioni di test Rust** in 12 file, type-safety quasi perfetta (`any` di produzione ≈ 0), **0 marcatori TODO/FIXME**. La stragrande maggioranza delle 16 macro-funzionalità dichiarate è **realmente implementata e coperta da test**.

I problemi reali non sono "feature mancanti", ma di altra natura:

1. **Documentazione che sovra-dichiara numeri specifici** — diversi valori nel README sono *fattualmente sbagliati* rispetto al codice (budget di contesto, bonus di ranking, hop del grafo, semantica del 4° segnale, colore archi, label hover). Sono i punti più pericolosi da riusare come spec: **vanno presi dal codice, non dal README**.
2. **Un solo item realmente "vaporware"**: F2 "il LLM suggerisce update a `purpose.md`" — non esiste alcun codice.
3. **Un buco di sicurezza concreto e serio**: il *clip server* su `127.0.0.1:19827` è **completamente non autenticato** (vettore drive-by write + prompt-injection + scrittura file in directory arbitraria). Contrasto netto con l'API principale `:19828`, che è invece ben hardenizzata.
4. **Layer UI non testato** (0 test di render dei 52 componenti `.tsx`), **CI che NON esegue i test** (solo build), **nessun linter** configurato.
5. **Caratteristiche di performance** da conoscere: il grafo viene **ricostruito interamente** ad ogni `dataVersion` (nessun indice incrementale) e ForceAtlas2 **blocca il main thread sotto i 220 nodi**.

### Tabella di sintesi (16 macro-funzionalità)

| # | Funzionalità (claim README) | Stato | Nota chiave |
|---|---|---|---|
| 1 | Three-column layout + icon sidebar + resizable + activity panel | ✅ Completo | Sidebar parte da *Chat*, non Wiki; resize manuale con clamp min/max |
| 2 | `purpose.md` (letto in ogni ingest/query + suggerimenti di update) | 🟡 Parziale | Letto ✅ ovunque; **"suggerisce update" ❌ assente**; bug path `purpose.md` vs `wiki/purpose.md` |
| 3 | Two-step CoT ingest (Analysis → Generation) | ✅ Completo | Due `streamChat` distinti, tracciabilità, fallback, language-aware |
| 4 | Knowledge Graph 4-signal + sigma/FA2 | ✅ Completo (pesi esatti) | README **inaccurato** su 3 dettagli (vedi §F4); FA2 blocca main thread <220 nodi |
| 5 | Query retrieval 4-fasi (budget 60/20/5/15, citazioni) | 🟡 Parziale | Funziona ma **4 numeri/claim del README sono falsi** (vedi §F5) |
| 6 | Multi-conversation chat + regenerate + Save to Wiki | ✅ Completo | Persistenza per-conversazione, refs persistite |
| 7 | Thinking/reasoning `<think>` display | ✅ Completo | Roll 5 righe, collapse, multi-provider reasoning fields |
| 8 | LaTeX → Unicode (100+ simboli) | ✅ Completo | **168 mapping** (~145 glifi distinti) + KaTeX confermato |
| 9 | Review System async (Create/Deep Research/Skip) | ✅ Completo | "azioni vincolate" **sovra-dichiarato** (matching euristico) |
| 10 | Deep Research (web search, queue 3, auto-ingest) | 🟡 Parziale | Concurrency=3 ✅, 6 provider; **"full content extraction" FALSO** (solo snippet) |
| 11 | Browser extension (MV3, Readability, Turndown, :19827, watcher 3s) | ✅ Funzionale / ❌ **Sicurezza** | Clip server **non autenticato** — vedi §F11 e doc 02 |
| 12 | Multi-format docs (PDF/DOCX/PPTX/XLSX/ODS) | ✅ Completo | PDF via **`pdfium-render`**, non `pdf-extract` (nome crate errato nel README) |
| 13 | File deletion cascade cleanup | ✅ Completo | 3-method matching, shared-entity preservation, dead-link cleanup — ben testato |
| 14 | Configurable context window (4K–1M) | 🟡 Completo con caveat | Split reale **50/5/15/30**, non 60/20/5/15; label UI dice 60% ma alloca 55% |
| 15 | Cross-platform (normalizePath, unicode-safe, CI multi-OS) | ✅ Completo | `normalizePath` in 51 file; close-behavior **configurabile** (non hardcoded); CI 4 OS/arch **ma non esegue test** |
| 16 | i18n / settings / multi-provider / 15-min timeout / dataVersion | ✅ Completo (eccede) | **7 provider** (+Azure/MiniMax) + CLI transport; **timeout 30 min, non 15** |

Legenda: ✅ Completo · 🟡 Parziale · ❌ Assente/stub.

---

## 1. Correzioni fattuali al brief (claim del task ≠ codice)

Prima dell'analisi feature-by-feature, sei discrepanze che il brief di sessione dà per buone ma il codice smentisce. Riportarle perché impattano direttamente l'uso come spec:

| Affermazione del brief | Realtà nel codice | Evidenza |
|---|---|---|
| "Local HTTP API porta 19827" | **Due server distinti**: API principale token-protected su **`:19828`**; clip/extension server su **`:19827`** | `api_server.rs:19` · `clip_server.rs:17` · `popup.js:1` |
| "Deep Research (Tavily API)" | **6 provider**: Tavily, SerpApi, SearXNG, **Brave, Firecrawl, Ollama** | `web-search.ts:156-171` |
| "Milkdown" come editor | Confermato (`@milkdown/kit ^7.20`) — è WYSIWYG ProseMirror-based | `package.json:23` |
| "15-minute timeout" | **30 minuti** | `llm-client.ts:96` (`30 * 60 * 1000`) |
| "macOS close-to-hide / Windows close-confirm" hardcoded | **Impostazione utente** (`ask`/`minimize`/`exit`, default `minimize`) | `lib.rs:262-307` · `general-section.tsx:11-27` |
| "PDF via pdf-extract" | **`pdfium-render` 0.9** (FFI pdfium); `pdf-extract` non è tra le dipendenze | `Cargo.toml:29` |

E discrepanze README↔README_CN: **nessuna feature documentata solo in cinese**. README_CN ha le stesse 18 sezioni (mapping 1:1) e cita gli stessi due port (19828/19827). README_JA e README_KO sono anch'essi traduzioni allineate.

---

## 2. Audit per funzionalità

Ogni voce: **Stato**, **Evidenza** (`file:riga` + estratti), **Gap vs README**, **Bug/edge case**.

---

### F1 — Three-column layout + icon sidebar + resizable + activity panel — ✅ COMPLETO

**Evidenza**
- Shell a 3 colonne: `app-layout.tsx:86-143` — sinistra `SidebarPanel`+`ActivityPanel` (L100-108), centro `ContentArea` (L117-121), destra `ResearchPanel` (L124-139).
- Icon sidebar: `icon-sidebar.tsx:20-28` `NAV_ITEMS` = Chat/Wiki/Sources/Search/Graph/Lint/Review; + Deep Research (Globe) L111-128; + Settings L151 + Switch-Project L182.
- Resize con vincoli: `app-layout.tsx:53-62` — sinistra `Math.max(150, Math.min(400, …))`; destra `Math.max(250, Math.min(rect.width*0.5, …))`. (Resize implementato a mano via `mousemove`, non via `react-resizable-panels` per il layout principale.)
- Activity panel real-time: `activity-panel.tsx` — poll coda ogni **1s** (L89-94), testo stato live (L194-211), progress bar (L316-321), auto-expand su task running (L179-187), retry/cancel/pause per task.
- Scenario templates (5): `templates.ts:640-646` — Research / Reading / Personal Growth / Business / General, ognuno con `schema` + `purpose` completi + `extraDirs`. Picker `template-picker.tsx:10-41`.

**Gap vs README** — Nessuno sostanziale. L'ordine icone parte da *Chat* (il README lascia intendere Wiki-first). Irrilevante.

**Bug/edge case** — Nessuno UI-layer testato (vedi doc 02): il layout, drag-resize incluso, non ha test di render.

---

### F2 — `purpose.md` (letto in ogni ingest/query + suggerimenti di update) — 🟡 PARZIALE

**Evidenza — letto su ingest e query: SÌ**
- Ingest: `ingest.ts:691-694` legge `${pp}/purpose.md` e lo inietta in entrambe le fasi (analysis `:968`, generation `:1000`).
- Query/chat: `chat-agent.ts:1005` e `:1654`; graph-query `graph-view.tsx:822`.

**Evidenza — "il LLM suggerisce update a `purpose.md`": ❌ ASSENTE (vaporware)**
- L'**unica** scrittura di `purpose.md` in tutto il codice è alla creazione progetto da template: `create-project-dialog.tsx:120` (`writeFile(\`${pp}/purpose.md\`, template.purpose)`). Nessun prompt chiede al modello di revisionare il purpose; nessun blocco FILE lo targetizza. Il claim README §2 ("LLM can suggest updates based on usage patterns") **non ha implementazione**.

**Bug — divergenza di path tra i due entry-point di ingest**
- `autoIngest` (coda) legge `purpose.md` a root (`ingest.ts:694`) ma `wiki/schema.md`.
- `startIngest` (handoff interattivo) legge `wiki/purpose.md` e `wiki/schema.md` (`ingest.ts:2982-2986`).
- Poiché il template scrive a root `purpose.md`, il path interattivo legge un `wiki/purpose.md` che normalmente **non esiste** → contesto purpose silenziosamente vuoto su quel ramo.

**Verdetto** — Lettura completa e corretta; *suggerimento di update assente*; bug di path da sanare.

---

### F3 — Two-step Chain-of-Thought ingest — ✅ COMPLETO

**Evidenza**
- **Due chiamate LLM distinte**: Step 1 Analysis `ingest.ts:953-981` (`streamChat` separato, `buildAnalysisPrompt`); Step 2 Generation `:991-1039` (`buildGenerationPrompt`, riceve l'analisi Stage-1 come contesto). Entrambe `temperature:0.1`, `reasoning:off`.
- **Source traceability** (`sources[]`): prompt impone il filename in `sources` (`:2002`, `:2048`); a write-time `canonicalizeSourcesField(content, sourceFileName)` lo forza (`:1731`). Cascade-delete usa `sources[]` come chiave (`source-lifecycle.ts:338-360`).
- **`overview.md` rigenerata** ad ogni ingest (overwrite completo, non incrementale): prompt item 6 (`:2024`), esente da date-stamping (`:1369-1375`).
- **Fallback source-summary garantito** (condizionale): `ingest.ts:1219-1244` — se nessun blocco FILE source-summary è stato scritto E non si è abortito, scrive uno stub con frontmatter + `analysis.slice(0,3000)`. *Non* è incondizionato: su `signal.aborted` viene saltato (per design, così l'abort innesca retry).
- **Language-aware** (2 livelli): prompt-level `buildLanguageDirective` inietta `MANDATORY OUTPUT LANGUAGE: X` in entrambe le fasi (testato `ingest.prompt.test.ts:18-185`); write-level `contentMatchesTargetLanguage` (`:1338-1363`) scarta soft i mismatch cross-family.

**Gap vs README** — Nessuno. Anzi, eccede il README con la `SHA256 incremental cache`, queue persistente, folder import, auto-watch (sotto).

**Bug/edge case**
- `overview.md` viene **riscritta integralmente** dal LLM ad ogni ingest: rischio drift/omissione se il modello dimentica topic pregressi (nessun merge-guard sull'overview).
- Il language-guard accetta qualsiasi non-CJK per target Latin (`:1362` `return !detectedIsCjk`): output francese per target inglese passa (documentato come accettabile `:1352-1354`).

#### F3-bis — SHA256 incremental cache — ✅ COMPLETO (con dubbio su read-side)
- `ingest-cache.ts`: `sha256` via `crypto.subtle` (`:20-26`); cache `.llm-wiki/ingest-cache.json` keyed per filename → `{hash, timestamp, filesWritten}`. `checkIngestCache` ritorna la lista cached **solo** se l'hash combacia E ogni file precedente esiste ancora (`:74-92`, anti-ghost). Scritta solo su `writtenPaths>0 && hardFailures===0` (`ingest.ts:1282-1283`).
- **Caveat**: chiave per basename/identità, **non content-addressed** → due file omonimi in cartelle diverse possono collidere. `saveCache`/`loadCache` ingoiano gli errori (`:36`, `:44`). **Da verificare**: nel ramo coda lo skip-unchanged sembra basarsi sul layer MD5 di `scheduled-import.ts:389`, non sulla SHA256 cache — i *caller* di `checkIngestCache` nel flusso `autoIngest` non risultano evidenti: possibile read-side under-used.

#### F3-ter — Persistent ingest queue — ✅ COMPLETO (senza backoff)
- `ingest-queue.ts`: seriale (`processing` flag + `processNext` ricorsivo `:658-821`), persistita `.llm-wiki/ingest-queue.json` (pending+failed). Crash recovery: `restoreQueue` resetta `processing→pending` ma li tiene in `restoredPausedTaskIds` per **non** auto-eseguirli (`:582-596`, evita spese a sorpresa). Cancel: `cancelTask`/`cancelAllTasks` abortiscono l'`AbortController` + cascade-delete dei file parziali (`:303-377`). Retry: `MAX_RETRIES=3` (`:610`, `:805-814`).
- **Bug**: retry **senza backoff** — su fallimento `retryCount++` e immediato `processNext` (`:805-820`): una source che fallisce in modo deterministico brucia 3 chiamate LLM consecutive. Solo errori 429/usage-limit ottengono il timer di resume a 15 min (`:621-634`).
- **Race**: globali module-level (`queue`, `processing`, `lastWrittenFiles`, `currentAbortController`) → le guard post-`await` (`currentProjectId !== projectId`) sono *load-bearing*; un cancel tra le scritture di un runner orfano e la sua guard potrebbe cascade-deletare i file del progetto sbagliato (fortemente mitigato, ma fragile).

#### F3-quater — Folder import — ✅ COMPLETO
- `source-lifecycle.ts:200-257`: ricorsivo (`flattenFiles`), preserva struttura in `raw/sources/<folder>/`, natural-sort, `folderContext` = path joinato con ` > ` come hint di classificazione (`folderContextForSourcePath:122-134`) iniettato nel messaggio di analysis (`ingest.ts:969`). Self-import guarded.

#### F3-quinquies — Source folder auto-watch — ✅ COMPLETO
- Backend `file_sync.rs`: `notify::RecommendedWatcher` ricorsivo (`:287-298`), debounce 700ms (`:215-220`), fallback full-rescan su overflow/errore (`:272-281`), worker panic-isolato (`:236-249`). Frontend `project-file-sync.ts`: listener `file-sync://changed`, secondo debounce 250ms (`:135-151`), dedup via `changeTaskKey` con set bounded 4096.
- **Bug**: doppio-watch (root ricorsivo + `raw/sources`/`wiki` ricorsivi → eventi duplicati, deduplicati ma sprecati); l'enqueue funziona **solo** sul progetto attivo (`ingest-queue.ts:218-222` throw altrimenti) → modifiche esterne a progetti non-attivi rilevate ma solo `console.error`-loggate.

#### F3-sexies — Scheduled import — ✅ COMPLETO
- `scheduled-import.ts`: `setInterval` clampato 1–1440 min, `runId` anti-stale, MD5-diff vs `.llm-wiki/scheduled-import-db.json`, cap 100MB/file.
- **Bug**: `scanning` è globale **non per-progetto** (`:51`,`:348`) → uno scan in volo droppa silenziosamente quello di un altro progetto; la import-db è marcata **dopo** l'enqueue (`:411-419`): se LLM non configurato, i file vengono ricopiati ad ogni intervallo (churn).

---

### F4 — Knowledge Graph 4-signal + sigma.js/ForceAtlas2 — ✅ COMPLETO (pesi esatti) · README inaccurato su 3 punti

**Evidenza — modello 4-segnali: pesi ESATTI**
`graph-relevance.ts:30-43`:
```ts
const WEIGHTS = { directLink: 3.0, sourceOverlap: 4.0, commonNeighbor: 1.5, typeAffinity: 1.0 } as const
```
Applicazione in `calculateRelevance` (`:247-287`): direct link **bidirezionale** (`:255-257`); source overlap = #sources condivise × 4.0 (`:260-265`); **Adamic-Adar** = `Σ 1/log(max(deg,2))` × 1.5 (`:270-280`, implementazione corretta); type affinity = `TYPE_AFFINITY[a][b] ?? 0.5` × 1.0 (`:283-284`).

**Evidenza — visualizzazione (sigma + graphology + FA2): completa**
- Deps: `@react-sigma/core ^5.0.6`, `sigma ^3.0.2`, `graphology ^0.26.0`, `graphology-layout-forceatlas2 ^0.10.1`.
- Color by type/community (`graph-view.tsx:203-208`, `:333-335`); size √ (`:232-237`); hover neighbor-highlight (`:557-562`, `:480-527`); zoom in/out/fit (`:584-623`); position cache anti-jump (`:301-302`, `:322-338`); legend type/community con marker low-cohesion (`:1373-1473`).

**Gap vs README — 3 inaccuratezze documentali (importanti per la spec)**
1. **Semantica del 4° segnale invertita.** README: "*Bonus for same page type* (entity↔entity, concept↔concept)". La matrice reale `TYPE_AFFINITY` (`:37-43`) fa l'**opposto**: same-type ha i valori più bassi (entity↔entity 0.8, source↔source 0.5, query↔query 0.5) e premia il cross-type (entity↔concept 1.2). Il peso ×1.0 è giusto; la *descrizione* è sbagliata.
2. **Colore archi.** README: "green=strong, gray=weak". Reale: **monocromatico slate-500 con alpha variabile** (`rgba(100,116,139,alpha)`, `:359-360`). Nessun verde.
3. **Label relevance su hover archi: inesistente.** README `:150`: "edges highlight with relevance score label". Reale: `renderEdgeLabels:false` (`:478`,`:1113`) — gli archi si ricolorano/ispessiscono ma **nessuna label di score**.

**Bug/perf**
- **Rebuild totale del grafo ad ogni `dataVersion`**: `buildWikiGraph` ri-`listDirectory`+`readFile` di **ogni** `.md`, ri-Louvain, ricalcolo relevance per ogni arco. Nessun update incrementale → costo dominante su vault grandi. (Rilevante per Synapse-I1.)
- **FA2 sul main thread sotto 220 nodi**: worker reale solo per grafi `≥220` (`graph-view.tsx:83`,`:403-445`); per `1<n<220` `runMainThreadLayout()` gira sincrono (fino a 140 iter, `:374-399`) → blocco UI su grafi piccoli/medi.
- `positionCache` module-level mai evicted (`:301`) → leak minore tra cambi-progetto.
- Sigma viene **force-remountato** (`sigmaKey++`) su ogni resize/toggle (`:858-897`) per evitare un crash WebGL — workaround, non fix.
- **Nessun test** su `graph-relevance`, `graph-insights`, `wiki-graph`: i pesi F4 non sono presidiati da alcun test.

---

### F5 — Knowledge Graph: Louvain + Insights (estensioni README §5–6) — ✅ COMPLETO

**Louvain** (`wiki-graph.ts:31-113`): `louvain(g,{resolution:1})` (`:53`); cohesion = densità intra-edge `intraEdges/possibleEdges` (`:78-88`); toggle type/community (`graph-view.tsx:1049-1066`); warning <0.15 (`:1464-1466`); palette **12 colori** (`:52-65`); community ri-numerate per size (`:99-110`). Perf: cohesion O(n²) per community.

**Graph Insights** (`graph-insights.ts`): surprising connections con composite score, soglia ≥3 (`:31-102`, esclude index/log/overview); knowledge gaps = isolated (deg≤1), sparse community (cohesion<0.15 & ≥3 nodi, `:141`), bridge node (≥3 community, `:168-179`); dismissable (`graph-view.tsx:800-805`); click-to-highlight (`:1516`); Deep Research button su gap (`:1577-1588`, legge overview+purpose). Param morto `_communities` (`:34`).

---

### F5-query — Query retrieval pipeline a 4 fasi — 🟡 PARZIALE (funziona, ma 4 claim del README sono falsi)

> **Architettura reale**: non è una pipeline lineare a 4 fasi, ma un **loop agentico LLM-driven** in `chat-agent.ts` (`buildChatAgentMessages`, chiamato da `chat-panel.tsx:217`). Un "router" LLM decide quali tool girare (`wiki_search`, `graph_search`, `external_search`…), poi i risultati vengono fusi/budgettati/assemblati. Le "Fasi 1→4" del README descrivono bene le *capacità*, ma non esiste una funzione lineare a 4 fasi.

| Fase | Stato | Evidenza & discrepanza |
|---|---|---|
| **1 — Tokenized search** | ✅ (in **Rust**) | Tokenizer Rust `search.rs:488-520` (lowercase, split punteggiatura, stopword EN+CJK `:545-590`, **bigrammi CJK** `:501-511`). Il `tokenizeQuery` TS (`search.ts:37-59`) è usato **solo** per filtrare image-caption. Scoring `search.rs:14-21`: `FILENAME_EXACT_BONUS=200`, `PHRASE_IN_TITLE=50`, `PHRASE_IN_CONTENT=20/occ`, title-token 5.0, content-token 1.0. **➤ "Title match bonus (+10)" del README è FITTIZIO** (non esiste +10). **➤ Cerca solo `wiki/`, NON `raw/sources/`** (`search.rs:151-152`): il README attribuisce la ricerca a "wiki/ AND raw/sources/" — falso. |
| **1.5 — Vector / RRF** | ✅ | Embedding query lato Rust `resolve_query_embedding` (`search.rs:97-118`, timeout 8s, fallback graceful su `Ok(None)`). ANN su LanceDB `wiki_chunks_v2` (`vectorstore.rs:533`). **Fusione RRF** `apply_rrf_scores` (`search.rs:274-295`, `RRF_K=60`). **➤ "cosine similarity" del README è in realtà L2-derived `1/(1+distance)`** (`vectorstore.rs:612`): nessuna metrica cosine settata su LanceDB. In modalità hybrid l'RRF **scarta** lo score keyword ricco (200/50/20) sostituendolo col rank-reciprocal. |
| **2 — Graph expansion** | 🟡 | `runGraphSearchTool` (`chat-agent.ts:1194-1252`): keyword top-6 → `getRelatedNodes(id,graph,5)` → relevance≥1.5 → top-8. I pesi 4-segnali sono esatti. **➤ "2-hop traversal with decay" del README è FALSO: il codice è strettamente 1-hop, senza decay** (`graph-relevance.ts:289-308`). |
| **3 — Budget control** | 🟡 | `context-budget.ts:54-59`: `RESPONSE_RESERVE=0.15`, `INDEX=0.05`, `PAGE=0.5`, `PER_PAGE=0.3` (floor 5000). **➤ "60/20/5/15 (wiki/chat-history/index/system)" del README è FALSO. Reale: pagine 50%, index 5%, response-reserve 15%, ~30% headroom. Non esiste budget a byte per la chat-history** (è gated per *conteggio* messaggi `maxHistoryMessages`, default 10). Confermato anche da `context-budget.test.ts:78` ("100%−5%−50%−15%=30%"). |
| **4 — Context assembly + citazioni** | ✅ | `buildRetrievedContext` (`chat-agent.ts:1479-1517`): blocchi numerati `<context id="1">`, ID locali `[1][2]`, esterni `[E1][E2]`, cap per item; system prompt impone citazioni inline + trailer nascosto `<!-- cited: 1,3 -->` (`:1451`). |

**Verdetto F5** — La *pipeline funziona ed è ben fatta*, ma **quattro affermazioni numeriche del README sono inesatte** (+10, raw/sources, 2-hop+decay, 60/20/5/15) e una di metrica (cosine→L2). Per la spec: **fidarsi del codice**.

---

### F6 — Multi-conversation chat + persistenza + regenerate + Save to Wiki — ✅ COMPLETO

**Evidenza**
- Create/rename/delete/setActive: `chat-store.ts:108-145`; ID `conv_{ts}_{rand}` (`:92`); auto-title dai primi 50 char (`:174`).
- **Persistenza** `.llm-wiki/chats/{id}.json` (`persist.ts:91`; index `conversations.json` `:73`; delete file `chat-panel.tsx:101`), testata `persist.integration.test.ts:217-229`. References persistite nel message data (`chat-store.ts:40`,`:202-220`).
- History depth default **10** (`chat-store.ts:103`), `.slice(-maxHistoryMessages)` (`chat-panel.tsx:214`).
- Cited-references panel collapsible con icone (`chat-message.tsx:423`); fallback al parsing del commento `<!-- cited -->` (`:446-450`).
- **Regenerate** (`chat-panel.tsx:331-356`): rimuove last-assistant + last-user, re-invia testo **+ immagini**.
- **Save to Wiki** (`chat-message.tsx:236-309`): scrive `wiki/queries/`, aggiorna `index.md`/`log.md`, bump dataVersion, poi `autoIngest`. Contenuto ripulito da think-block/commenti via `cleanAssistantContentForWikiSave`.

**Bug/edge case**
- Regenerate usa un `setTimeout(50ms)` "to let state update" (`:340`) — hack di timing fragile invece di await sullo stato.
- Se l'ultimo turno è user-only (assistant abortito), `removeLastAssistantMessage` no-op poi rimuove+riaggiunge l'user → comportamento border-line ma tollerato.

---

### F7 — Thinking / reasoning display — ✅ COMPLETO

**Evidenza**
- `separateThinking` (`chat-message.tsx:1031-1051`): regex `<think(?:ing)?>…`, gestisce blocchi multipli + tag non chiuso in streaming.
- Streaming roll **5 righe** + opacity fade (`StreamingThinkingBlock:1055-1080`); collapsed-by-default (`ThinkingBlock:1083-1107`); stile amber = separazione visiva.
- Routing reasoning (`reasoning-detector.ts:50-87`): parse `reasoning_content` (DeepSeek/Kimi), `reasoning` (Qwen), Anthropic `thinking_delta`, Gemini `thought`. Trigger **per presenza di campo nello stream**, non per allowlist di nomi-modello.
- Fallback bounded: su `isReasoningOnlyResponseError`, retry singolo con `reasoning:{mode:"off"}` (`chat-panel.tsx:286-296`).

**Gap** — README cita "DeepSeek, QwQ"; il detector copre DeepSeek-R1, Kimi K2.x, Qwen, Anthropic, Gemini. Superset.

---

### F8 — LaTeX → Unicode + KaTeX — ✅ COMPLETO

**Evidenza**
- `latex-to-unicode.ts`: `LATEX_TO_UNICODE` con **168 entry** (contate), di cui ~12 comandi di formattazione → `""` e ~10 alias (`le`/`leq`…) → **~145 glifi distinti**. Il claim "100+" è soddisfatto (e conservativo).
- `convertLatexToUnicode` (`:58-75`): gestisce `$\cmd$`, `$$…$$`, `$…$` inline; comandi ignoti passano through.
- **KaTeX confermato**: `chat-message.tsx:5-7` importa `remarkMath`+`rehypeKatex`+CSS; applicato a `ReactMarkdown` (`:960`). Auto-wrap di `\begin{…}\end{…}` bare con `$$` (`:1123-1126`); Unicode-fallback applicato **solo fuori** dai blocchi math (`:1130-1135`).

---

### F9 — Review System async (Create / Deep Research / Skip) — ✅ COMPLETO (claim "azioni vincolate" sovra-dichiarato)

**Evidenza**
- Item emessi a ingest-time come blocchi `---REVIEW:…---END REVIEW---` (`ingest.ts:1840`), parsati e pushati allo store **non-bloccante** (`:1269-1270`); persistiti (`review-view.tsx:54`).
- **Query di ricerca pre-generate a ingest-time** (claim distintivo di F9): prompt impone una `SEARCH:` line (`ingest.ts:2178`), parsata in `searchQueries` (`:1879-1882`), consumata da `queueResearch` (`review-view.tsx:79`).
- Enum tipo vincolato: `review-store.ts:11` (`contradiction|duplicate|missing-page|confirm|suggestion`), tipo ignoto → coerced a `confirm` (`ingest.ts:1854-1858`). Deep Research è sentinel hard `__deep_research__` (`review-view.tsx:69`,`:476`).
- **Sweep-reviews** (auto-resolve su drain coda): trigger `ingest-queue.ts:647-648`; stage 1 rule-based (`sweep-reviews.ts:362-396`), stage 2 LLM-judgment bounded `JUDGE_BATCH_SIZE=40`/`MAX_JUDGE_BATCHES=5`/`MAX_PAGES_IN_PROMPT=300` (`:180-182`), fallback conservativo a set vuoto.
- ID content-derived (FNV-1a, `review-store.ts:49-58`) → stato "resolved" stabile tra re-ingest.

**Gap vs README** — README §11: "constrained to prevent LLM hallucination of arbitrary actions" — **sovra-dichiarato**. Le *label* azione sono testo LLM libero: `parseReviewBlocks` prende la `OPTIONS:` line verbatim (`ingest.ts:1861-1870`) e il handler fa **matching euristico fuzzy** (`actionLooksLikeResearch`/`actionLooksLikeCreate`… `review-view.tsx:504-548`). Solo `__deep_research__` e l'enum-tipo sono davvero vincolati; lo spazio azioni è *interpretato*, non *enumerato*.

**Bug/edge case**
- La race "switch progetto a metà sweep" è **già fixata e presidiata da test** (`sweep-reviews.race.test.ts`): pre-check (`:344-345`), recheck post-`buildWikiIndex` (`:356`), guard per-iterazione (`:365`), guard finale (`:418`). Residuo non coperto: un bail mid-loop dopo aver già `resolveItem`-ato item precedenti **non fa rollback** dei write parziali (basso impatto, reversibile).

---

### F10 — Deep Research — 🟡 PARZIALE (un claim README FALSO)

**Evidenza**
- **6 provider** (README ne dichiara 3): switch `web-search.ts:156-171` → Tavily (`:261`), SerpApi (`:434`, 9 engine), SearXNG (`:187`) + Brave (`:605`), Firecrawl (`:314`), Ollama (`:529`).
- **Concurrency = 3** confermata: `research-store.ts:33` `maxConcurrent:3`, enforced `deep-research.ts:173-180`; `getRunningCount` conta `searching|synthesizing|saving`.
- Multi-query per topic (`:118-125`, dedup per URL); topic LLM-optimized che legge overview+purpose (`optimize-research-topic.ts:14`,`:27-30`); dialog editabile (`graph-view.tsx:1608-1690`); sintesi → `wiki/queries/` con frontmatter `origin:deep-research` (`:305-324`); `<think>` reso collapsible (`research-panel.tsx:113-178`); **auto-ingest** dei risultati (`deep-research.ts:339-343`); streaming progress per-token (`:265-269`).

**Gap vs README — claim FALSO**
- README §12: "full content extraction (no truncation)". **Falso/fuorviante**: la sintesi usa **solo** lo `snippet`/`content` breve del provider (`deep-research.ts:223-225`); **non esiste alcuno step di fetch/estrazione della pagina** nel path Deep Research. Tavily usa `search_depth:"advanced"` ma **non** setta `include_raw_content` (`web-search.ts:278-280`). La vera estrazione Readability esiste solo nel web-clipper (F11), path diverso.

**Bug/edge case**
- **Possibile double-dispatch** quando `available≥2` e ≥2 task in coda: `processQueue` lancia `executeResearch` senza await e senza marcare sincronicamente il task come non-`queued`; lo status `searching` viene settato **dentro** il corpo async (`:196`), dopo che il loop di lancio sincrono è già passato → `getNextQueued()` può restituire lo **stesso** task ancora `queued`. Nessun test copre `available≥2`. Da verificare.
- **Auto-ingest fire-and-forget**: `.catch(console.error)` (`:340-342`) → un fallimento di ingest è invisibile, il task resta `done`/`saved` ma entità/concetti non estratti.
- **Nessun `token_budget` / cost cap** sui loop synthesis/optimize (cap solo su #sources `MAX_RESEARCH_SOURCES=20`). Rilevante per l'invariante Synapse-I7.

---

### F11 — Browser extension (Web Clipper) — ✅ FUNZIONALE / ❌ SICUREZZA

**Evidenza funzionale (completa)**
- Manifest V3 (`manifest.json:2`); Readability+Turndown iniettati e girati in-page (`popup.js:94-107`, table rules `:129-141`); project picker da `GET /projects` (`popup.html:124`, `popup.js:55-69`); offline preview (`popup.js:286-291`); auto-ingest via `PENDING_CLIPS` (`clip_server.rs:430-432`) → clip-watcher `enqueueIngest` (`clip-watcher.ts:39-43`); **polling 3s** (`clip-watcher.ts:6`); idempotency su POST `/clip` (`popup.js:18-21`).

**Port confermati**: clip/extension server **`:19827`** (`clip_server.rs:17`, `popup.js:1`, `manifest.json:8-9`, `clip-watcher.ts:18`); API principale **`:19828`** (separata).

**❌ SICUREZZA (dettaglio completo in doc 02 — qui solo il sommario)**
- **Il clip server `:19827` è completamente non autenticato** (`grep token|Authorization` → 0 match). Ogni endpoint (`/clip`, `/project`, `/projects`, `/clips/pending`) è aperto.
- **Drive-by write da qualsiasi sito**: un semplice `POST` `text/plain` non è bloccato da CORS (l'Origin è usato solo per gli header di risposta, non per autorizzare l'azione, `clip_server.rs:96-97`); qualsiasi pagina può scrivere `.md` in `raw/sources/` (`:379-416`), poi **auto-ingeriti dal LLM** → vettore di prompt-injection.
- **Scrittura in directory arbitraria**: `project_path` arriva dal body (`:335`) e viene usato in `Path::new(&project_path).join("raw").join("sources")` **senza `safe_join`/canonicalizzazione** (`:379-389`) — l'API `:19828` invece ha `safe_join` (`api_server.rs:784-824`).
- **Body senza cap** (`read_to_string` illimitato, vs `MAX_BODY_BYTES` dell'API).
- **Esposizione LAN**: il clip server eredita `configured_bind_host` (`clip_server.rs:54`) → con `allowLanAccess` bind su `0.0.0.0` **senza alcun token** → qualsiasi host della LAN può scrivere file + innescare ingest.

**Edge case `/clips/pending`**: la GET **drena e `clear()`** la coda (`clip_server.rs:234`); un clip per progetto non-attivo o con LLM non configurato (`clip-watcher.ts:31`,`:39`) viene drenato e **droppato silenziosamente**.

---

### F12 — Multi-format document support — ✅ COMPLETO

| Formato | Stato | Evidenza |
|---|---|---|
| PDF | ✅ via **`pdfium-render`** | `Cargo.toml:29`; dispatch `fs.rs:83` → `extract_images.rs:204`. **Nome crate README errato** ("pdf-extract"). |
| DOCX | ✅ | `docx-rs 0.4.20`; `fs.rs:448` headings/bold/italic/lists/tables; fallback ZIP `:570-572` |
| PPTX | ✅ | ZIP+XML slide-by-slide `fs.rs:786-845` |
| XLSX/XLS/ODS | ✅ | `calamine 0.34`; `fs.rs:857-924` cell-type, multi-sheet, MD tables |
| DOC (legacy) | ✅ | `office_oxide`; `fs.rs:431` |
| Immagini/Video/Audio | 🟡 by design | `read_file` ritorna placeholder; preview/player nel frontend (`file-types.ts`) |

- **Multimodal image ingestion** ✅: estrazione immagini PDF (`extract_images.rs:204/345/758`, min 100px, max 500/doc), caption vision-LLM con cache SHA-256 byte-keyed (`vision-caption.ts`, `image-caption-pipeline.ts:283`), search image-aware + lightbox + jump-to-source (`search-view.tsx:42-178`).
- **MinerU** ✅ con fallback corretto: su fallimento → `pdfium` (`ingest.ts:678-685`), abort **non** swallowed (`:679`); entrambi i path testati (`ingest-source-path-collision.test.ts:513`,`:545`).
- **File caching** ✅ mtime-based (`fs.rs:147-172`), invalidato su mtime (caveat: un file salvato con mtime backdated serve cache stale).
- **Blocking evitato**: tutto in `spawn_blocking`+`run_guarded` (`fs.rs:67,124`); pdfium serializzato via `PDFIUM_LOCK`. **Memoria**: parser leggono l'intero file in RAM (accettabile entro cap 200MB; rischio su input patologici).

---

### F13 — File deletion cascade cleanup — ✅ COMPLETO

**Evidenza — 3-method matching (Rust discovery + TS decision)**
- `collect_related_pages` (`fs.rs:1321`): (1) filename quotato in frontmatter `sources` (`:1367-1368`); (2) source-summary page sotto `sources/` con nome che inizia col source-stem (`:1372-1374`); (3) scan scoped del blocco YAML `sources:` (inline + continuation, `:1392-1423`, irrobustito contro falsi-positivi titolo/descrizione).
- **Shared-entity preservation** ✅ in due layer: `source-delete-decision.ts:33` (`survivors>0`→keep+rewrite; `==0`→delete) e `source-lifecycle.ts:344-360` (rewrite `sources[]` se sopravvivono altre source). Matching path-aware (`:527-539`) evita match cross-progetto.
- **index.md cleanup** + **dead `[[wikilink]]` cleanup** ✅ in `wiki-cleanup.ts`: `cleanIndexListing:98` rimuove per parse strutturale (non substring); `stripDeletedWikilinks:123` sostituisce `[[deleted|alias]]`→`alias`. Cascade anche su embeddings (`removePageEmbedding`) e `wiki/media/<slug>/` (`wiki-page-delete.ts:93-106`, guard anti `slug==="."`).
- **Test forti**: `wiki-cleanup.test.ts` (39 casi), `wiki-page-delete.test.ts` (23), `source-delete-decision.test.ts` (11), `sources-tree-delete.test.ts` (14).

---

### F14 — Configurable context window — 🟡 COMPLETO con caveat numerico

**Evidenza**
- Selector preset discreto 4K/8K/16K/32K/64K/128K/200K/256K/512K/**1M** (`context-size-selector.tsx:1-12`). **Unità = caratteri**, non token.
- Allocatore `context-budget.ts:54-90`, ben testato (`context-budget.test.ts`), con clamp per-page `min(pageBudget, max(5000, pageBudget*0.3))`.

**Gap vs README**
- Split reale **5% index / 50% pagine / 15% response-reserve / ~30% headroom** — **NON** "60/20/5/15".
- **Mismatch UI↔allocatore**: la label `context-size-selector.tsx:39` mostra "~{value*0.6/1000}K chars for wiki content" (**60%**) ma l'allocatore dà al wiki solo **55%** (50% pagine + 5% index). Sovrastima ~9%.

---

### F15 — Cross-platform — ✅ COMPLETO

**Evidenza**
- `normalizePath()` (`path-utils.ts:5`) referenziato in **51 file** (48 import diretti) — **niente reimplementazione TS** (la duplicazione esiste solo lato Rust, vedi doc 02).
- Unicode-safe slicing: TS `Array.from(slug).slice(0,50).join("")` (`wiki-filename.ts:44`); Rust `.chars().take(N)` (`clip_server.rs:375`, `search.rs:411`).
- Close behavior **configurabile** (`ask`/`minimize`/`exit`, default `minimize`) `lib.rs:262-307` + `general-section.tsx:11-27`; macOS `RunEvent::Reopen` re-show (`lib.rs:312-325`).
- **CI multi-OS/arch**: `build.yml:16-31` matrix = macOS arm64, Ubuntu x86_64, Ubuntu ARM64, Windows; bundle dmg/deb/AppImage/msi/nsis + portable Windows + extension zip. `ci.yml:11-14` build-check su 3 OS.

**Caveat critico (vedi doc 02/03)** — la CI **non esegue alcun test** (`npm test`/`cargo test` assenti): fa solo `vite build` + `cargo build`.

---

### F16 — i18n / settings / multi-provider / timeout / dataVersion — ✅ COMPLETO (eccede)

**Evidenza**
- i18n EN+ZH react-i18next; `en.json`/`zh.json` **673/673 chiavi, parità perfetta** (`i18n-parity.test.ts`).
- Settings via Tauri Store (`project-store.ts:1,13`, `load(...,{autoSave:true})`); plugin registrato `lib.rs:142`.
- `.obsidian/` auto-generato **in Rust** (`project.rs:196-233`: `app.json`/`appearance.json`/`core-plugins.json`).
- GFM via `remark-gfm` in 4 renderer (`file-preview`/`wiki-reader`/`chat-message`/`research-panel`).
- **Multi-provider (eccede i 5 dichiarati)**: `llm-providers.ts:801-993` — OpenAI/Anthropic/Google/Ollama/**Azure/MiniMax**/Custom + transport **CLI** (`claude-cli`, `codex-cli`, `commands/claude_cli.rs`, `codex_cli.rs`), parser stream e header per-provider, quirk vendor (DeepSeek/Kimi/Qwen/GLM/MiMo/GPT-5).
- **Timeout = 30 min** (`llm-client.ts:96`), non 15.
- `dataVersion` (`wiki-store.ts:385/439/567`) consumato da graph/tree/chat.

---

## 3. Cross-check con il pattern Karpathy (`llm-wiki.md`)

Dove il progetto **aderisce**, **estende** o **diverge** dal documento originale.

| Aspetto Karpathy | llm_wiki | Giudizio |
|---|---|---|
| Three-layer: raw (immutable) → wiki (LLM) → schema | Rispettato: `raw/sources/` immutabile, `wiki/` generato, `schema.md` da template | ✅ Fedele |
| Tre operazioni: Ingest / Query / Lint | Tutte presenti (lint in `lib/lint.ts`, view dedicata) | ✅ Fedele |
| `index.md` content-catalog, navigazione index-first | Presente e aggiornato a ogni ingest | ✅ Fedele |
| `log.md` append-only, parseable `## [date]` | Presente; voci append a ogni operazione | ✅ Fedele |
| `[[wikilink]]` + parser dedicato | `wiki-graph.ts`/`graph-relevance.ts` con regex + resolver; transform render-time `wikilink-transform.ts` | ✅ Fedele |
| YAML frontmatter pivot (type/title/sources[]) | Sì, pivot per grafo + cascade-delete | ✅ Fedele |
| **"index file is enough at moderate scale, no embedding RAG"** | Aggiunge **vector search (LanceDB) + grafo 4-segnali** | ⚖️ **Diverge in positivo** (scala meglio) ma **contro la minimalità** Karpathy; più complessità/infra |
| **"ingest one at a time, stay involved"** (human-in-loop sincrono) | Ingest **async/queue/auto-watch**; il controllo umano è spostato sulla **Review queue async (F9)** | ⚖️ Diverge: da curation sincrona a review asincrona — più automazione, meno supervisione in-the-moment |
| Buone risposte rifilate nel wiki come pagine | **Save to Wiki** (F6) → `wiki/queries/` + auto-ingest | ✅ Fedele (ben implementato) |
| Lint: contraddizioni, stale, orfani, gap, cross-ref mancanti | Lint + **sweep-reviews** + Graph Insights (gap/orfani/bridge) | ✅ **Estende** bene |
| **"Obsidian è l'IDE, il LLM è il programmatore"** (vault accanto a Obsidian) | App **standalone** con editor Milkdown proprio (Obsidian-lite), pur mantenendo vault-compat | ⚖️ Diverge: **reimplementa** Obsidian invece di affiancarlo. Pro: esperienza integrata; contro: deve reggere il confronto con Obsidian (editor, graph view) |
| **schema.md co-evoluto da umano + LLM** | `schema.md` generato da template, **statico** dopo creazione (come purpose.md, nessun suggerimento LLM) | ❌ **Diverge in negativo**: manca la co-evoluzione schema/purpose che Karpathy mette al centro |
| Web Clipper (suggerito: Obsidian Web Clipper) | Estensione Chrome **propria** (MV3) | ✅ Estende (ma con i problemi di sicurezza di §F11) |
| CLI search opzionale (es. qmd) | Search proprio + **MCP server** + API locale | ✅ Fedele allo spirito ("costruisci tool quando serve") |

**Sintesi cross-check** — Il progetto è **fedele al pattern e lo estende sostanzialmente** (grafo, vector, multimodale, deep research, multi-format). Le **due divergenze concettuali da segnalare** per Synapse: (a) la perdita della *co-evoluzione di schema/purpose* (item vaporware F2 + schema statico), che è uno dei cardini Karpathy; (b) lo spostamento dell'app da *companion di Obsidian* a *sostituto di Obsidian*, scelta legittima ma che alza l'asticella UX (l'editor Milkdown e la graph view devono competere con Obsidian, non solo essere "compatibili").

---

## 4. Verdetto finale FASE 1

- **13/16 macro-funzionalità: ✅ COMPLETE** e in larga parte coperte da test.
- **3/16: 🟡 PARZIALI** — F2 (suggerimenti purpose assenti), F5-query (4 numeri/claim falsi vs README), F10 (full-content-extraction falso), F14 (split numerico diverso). *(F11 è funzionalmente completo ma con una falla di sicurezza che lo declassa operativamente — trattato come ❌-sicurezza in doc 02.)*
- **1 vero vaporware**: F2 "LLM suggerisce update a purpose.md".
- **Rischio principale nel riusare il README come spec**: i numeri. I valori autoritativi sono nel codice: pesi grafo `graph-relevance.ts:30-43`, scoring `search.rs:14-21`, budget `context-budget.ts:54-59`.

I dettagli di code-quality, sicurezza, performance e UX sono nel documento **`02-CODE-UI-REVIEW.md`**; il piano di rientro a v1.0 in **`03-PIANO-AGENTICO-v1.0.md`**.
