# LLM Wiki (`nashsu/llm_wiki` v0.5.4) — Code Quality & UI/UX Review

> Review da senior full-stack reviewer + UX hacker. Ogni finding è ancorato a `file:riga`, con **severità** (🔴 Critico / 🟠 Alto / 🟡 Medio / 🔵 Basso) e **effort di fix** stimato (S ≤ ½ giornata · M 1–2 giorni · L 3–5 giorni · XL > 1 settimana).
> Companion di `01-AUDIT-FUNZIONALE.md`. Le verità di base: codebase matura (~95k LOC, 121 test TS + ~190 test Rust), type-safety quasi perfetta, **ma** layer UI non testato, CI che non esegue test, un clip server insicuro.

---

## 0. Sommario per severità

| Sev | # | Finding principali |
|---|---|---|
| 🔴 Critico | 4 | Clip server non autenticato (S-1), scrittura dir arbitraria (S-2), esposizione LAN unauth (S-3), CI non esegue i test (Q-1) |
| 🟠 Alto | 7 | Combo API unauth+LAN (S-4), body clip illimitato (S-5), 0 test UI/E2E (Q-2), retry senza backoff (Q-3), rebuild grafo totale (P-1), FA2 main-thread <220 (P-2), nessun linter (Q-4) |
| 🟡 Medio | 9 | Doc-vs-code (numeri falsi) (Q-5), clip non sanitizzato (S-6), double-dispatch research (B-1), auto-ingest silenzioso (B-2), `graph-view.tsx` monolite 1777 righe (A-1), purpose path bug (B-3), normalizePath Rust 3× divergenti (A-2), `scanning` globale (B-4), token in querystring opzionale (S-7) |
| 🔵 Basso | 8 | Cache mtime-only, positionCache leak, sweep partial-batch, MCP toggle client-side, sigma remount-churn, overview overwrite drift, label budget UI 60%≠55%, regenerate setTimeout hack |

**Tesi**: il *core business-logic* (Rust + `lib/` + `stores/`) è di buona qualità ingegneristica e ben testato. Il debito è concentrato in **(1) sicurezza del clip server**, **(2) gate di qualità assenti** (CI senza test, no linter, no test UI/E2E) e **(3) disallineamento doc↔codice**. Nessuno di questi è un riprogetto: sono interventi mirati.

---

## 1. Sicurezza

### S-1 🔴 Critico — Il clip server `:19827` è completamente non autenticato — *effort M*
`clip_server.rs` non ha alcun riferimento a token/Authorization (grep → 0 match). Tutti gli endpoint (`/clip`, `/project`, `/projects`, `/clips/pending`) sono aperti. Contrasto con l'API `:19828` che enforce `is_authorized` (`api_server.rs:246`).
**Impatto**: qualsiasi processo locale (e, con LAN on, qualsiasi host) interagisce col server senza credenziali.
**Fix**: condividere lo stesso token-gate dell'API `:19828` (header `X-LLM-Wiki-Token`/Bearer, confronto `constant_time_eq`); l'estensione lo include già potendolo leggere dall'app via handshake.

### S-2 🔴 Critico — Scrittura in directory arbitraria (path non validato) — *effort S*
`handle_clip` prende `project_path` dal body (`clip_server.rs:335`) e lo usa in `Path::new(&project_path).join("raw").join("sources")` con `create_dir_all` (`:379-389`) **senza** `safe_join`/canonicalizzazione. Lo slug del filename è sanificato (`:361-375`), ma la base è interamente controllata dal chiamante.
**Failure scenario**: `POST /clip {"projectPath":"/home/user/.config/autostart","content":"..."}` → crea `/home/user/.config/autostart/raw/sources/<slug>.md`. Scrittura file unauth in directory arbitraria (suffisso vincolato a `raw/sources/<slug>.md`).
**Fix**: validare `project_path` contro la lista dei progetti registrati; applicare lo stesso `safe_join` dell'API (`api_server.rs:784-824`).

### S-3 🔴 Critico — Drive-by write + prompt-injection da qualsiasi sito web — *effort M*
Una `POST` semplice (`Content-Type: text/plain`, no preflight) non è bloccata da CORS: l'Origin è usato **solo** per decidere gli header di risposta, mai per autorizzare l'azione (`clip_server.rs:96-97`); `handle_clip` parsa il body comunque (`:324-325`).
**Failure scenario**: un sito visitato esegue `fetch('http://127.0.0.1:19827/clip',{method:'POST',body:JSON.stringify({projectPath, content:'<payload>'})})` → scrive `.md` in `raw/sources/` → **auto-ingerito dal LLM** (`clip-watcher.ts:39`). Il payload è contenuto controllato dall'attaccante che diventa input del modello (prompt-injection). `/project` permette anche di ripuntare silenziosamente il "progetto corrente".
**Fix**: S-1 (token) risolve la maggior parte; in più validare `Origin`/`Host` contro un allowlist esplicito (`chrome-extension://<id>` + `127.0.0.1`), rifiutare richieste senza Origin atteso.

### S-4 🟠 Alto — Clip server eredita esposizione LAN senza token — *effort S*
`start_clip_server` usa `server_bind::configured_bind_host` (`clip_server.rs:54`): con `allowLanAccess` il server **non autenticato** bind su `0.0.0.0` (`server_bind.rs:22-31`). La UI dice "keep token auth enabled" (`api-server-section.tsx:302-310`) ma il clip server **non ha token**.
**Impatto**: su LAN, qualsiasi host scrive file + innesca ingest, unauth. (Una volta applicato S-1 questo si chiude; finché non lo è, è la combinazione più pericolosa.)

### S-5 🟠 Alto — Body del clip senza cap — *effort S*
`clip_server.rs:140,193,243` usano `read_to_string` **senza limite** (a differenza di `MAX_BODY_BYTES`=1 MiB dell'API, `api_server.rs:21,314-319`). DoS di memoria triviale con una POST grande.
**Fix**: `.take(MAX_BODY_BYTES)` come nell'API.

### S-6 🟡 Medio — Contenuto clippato non sanitizzato — *effort M*
`content` preso verbatim (`clip_server.rs:332`) e scritto nel markdown (`:406-414`) con escape solo di `"` nei campi frontmatter title/url; il body è raw. La sicurezza dipende interamente dal sanitizer del renderer GFM. Combinato con S-3, l'attaccante controlla il body completo.
**Fix**: sanitizzazione/escape del body, length-cap, e rendering con `rehype-sanitize` lato preview.

### S-7 🟡 Medio — Token API accettato anche via querystring — *effort S*
L'API `:19828` accetta il token via `?token=` oltre che header/Bearer (`api_server.rs:397-414`). La UI avvisa di non usarlo in URL (`api-server-section.tsx:461-465`), ma resta accettato → rischio di finire nei log del reverse proxy/cronologia.
**Fix**: deprecare `?token=` o gate dietro flag esplicito.

> **Positivi di sicurezza (da preservare, NON toccare)**: l'API `:19828` è ben hardenizzata — confronto token `constant_time_eq` timing-safe e fail-closed (`api_server.rs:399-414`,`:456-469`), kill-switch `api_enabled`→503, rate-limit 120/s→429, in-flight cap 64→503, `catch_unwind` per-richiesta→500, `safe_join` anti-traversal (`:784-824`), allowlist path pubblici, skip symlink, body cap. CORS è **allowlist** (no `*`, testato a rifiutare `localhost.evil.com`, `cors.rs:11`). Nessun secret nei log: `search.rs` strippa `x-goog-api-key` (`:797`,`:911`), `proxy.rs:126 redact_url()` maschera basic-auth. Token mai loggato. **L'intero problema di sicurezza è concentrato nel clip server `:19827`.**

---

## 2. Code quality — Rust (`src-tauri/`)

### Q-Rust-1 🟡 Medio — `normalizePath` reimplementato 3× in Rust con semantiche divergenti — *effort S*
TS ha un solo `normalizePath` (`path-utils.ts:5`, solo separatori). Rust ne ha tre con nomi e comportamenti diversi: `api_server.rs:689` (trimma anche trailing slash), `search.rs:1000` (solo separatori), `file_sync.rs:1385` (aggiunge case-fold). La divergenza trailing-slash dell'api_server è una superficie latente di bug di path-match.
**Fix**: estrarre un unico helper Rust condiviso con semantica documentata.

### Q-Rust-2 🔵 Basso — `Mutex::lock().unwrap()` negli handler HTTP del clip server — *effort S*
`clip_server.rs:123,164-165,196,215` fanno panic su mutex poisoned (auto-recupero via restart loop). Incoerente con `project.rs:34-45` che usa `.unwrap_or_default()`.
**Nota generale unwrap**: 288 occorrenze totali ma **~12 in produzione**, quasi tutte giustificate (header hardcoded `Header::from_bytes(...).unwrap()`, bootstrap `.expect()`, default compile-time, propagazione poison). **Nessun `panic!` in produzione**, nessun unwrap su body di rete o parse di file malformati (lì si usa `?`/`.ok()?`/`match`). La gestione panic è solida (`run_guarded`, `panic=unwind`, `catch_unwind` per-richiesta).

**Positivi Rust (preservare)**: lavoro sync pesante sempre in `spawn_blocking` (`fs.rs:67,124`), `block_on` solo su thread server dedicati (non worker tokio), nessun `reqwest::blocking`, pdfium serializzato con poison-recovery.

---

## 3. Code quality — TypeScript (`src/`)

### Q-TS-1 🟡 Medio — `graph-view.tsx` monolite da 1777 righe — *effort L*
Un singolo componente mescola layout, settings di rendering, insights panel, dialog research, editor embedded. Difficile da testare e mantenere; è anche la ragione del workaround `sigmaKey++` (remount per evitare crash WebGL, `:858-897`).
**Fix**: estrarre `GraphInsightsPanel`, `ResearchDialog`, `GraphLegend`, `useGraphLayout` in moduli separati e testabili.

### Q-TS-2 🔵 Basso — Logica duplicata grafo/wikilink in 2 moduli — *effort M*
`WIKILINK_REGEX`, `flattenMdFiles`, `extractWikilinks`, `fileNameToId`, `resolveTarget`, parsing frontmatter esistono **due volte** (`wiki-graph.ts` e `graph-relevance.ts`) con differenze sottili (es. `wiki-graph` non ha il parser `sources[]`). Rischio drift.

### Q-TS-3 🔵 Basso — Parsing frontmatter regex-based, non YAML — *effort M*
`graph-relevance.ts:71-122` usa regex per frontmatter/wikilink invece di `js-yaml` (già dipendenza!). Gestisce multi-line e inline `sources:` ma è fragile su quoting/nesting.

### Q-TS-4 🔵 Basso — Mismatch contratti serde↔TS (benigni) — *effort S*
`WikiProject` TS ha `id` (`wiki.ts:4`) assente dallo struct Rust (`wiki.rs:4-7`) — hydrated client-side; `ApiFileNode` (`api_server.rs:865`) usa `camelCase` (`isDir`) vs `FileNode` Tauri `snake_case` (`is_dir`). Funzionano, ma due shape parallele da tenere allineate a mano.

**Positivi TS (preservare)**: `any` di produzione ≈ 0 (i match sono la parola "any" in stringhe/commenti; gli unici `as any` sono mock vitest). `0` TODO/FIXME. Tipi `readonly`/`ReadonlySet`/frozen nel layer grafo. Gestione streaming chat con `runIdRef`+`AbortController`+`isCurrentRun()` (race-safe, `chat-panel.tsx:205-232`). Parsing LLM-output difensivo (`extractJsonObject` brace-aware, `sweep-reviews.ts:136-178`). Validazione `page_id` anti-injection prima di interpolarlo nei filtri LanceDB (`vectorstore.rs:98-114`, testata).

---

## 4. Bug funzionali (non-sicurezza)

| ID | Sev | Bug | Evidenza | Fix |
|---|---|---|---|---|
| B-1 | 🟡 | **Double-dispatch Deep Research** quando `available≥2` e ≥2 task in coda: lo status `searching` è settato dentro il corpo async (`:196`), dopo il loop di lancio sincrono → `getNextQueued()` può restituire lo stesso task `queued` due volte | `deep-research.ts:176-180,196` | marcare il task come non-`queued` **sincronicamente** prima del lancio; test con `available≥2` | 
| B-2 | 🟡 | **Auto-ingest fire-and-forget**: fallimento solo `console.error`, task resta `done` ma entità non estratte | `deep-research.ts:340-342` | surfacing errore + stato `ingest-failed` |
| B-3 | 🟡 | **Purpose path divergence**: `startIngest` legge `wiki/purpose.md` (inesistente) mentre il template scrive a root | `ingest.ts:2982-2986` vs `create-project-dialog.tsx:120` | unificare su root `purpose.md` |
| B-4 | 🟡 | **`scanning` globale non per-progetto**: uno scan in volo droppa quello di un altro progetto | `scheduled-import.ts:51,348` | mappa `scanning` per projectId |
| B-5 | 🔵 | **Retry senza backoff**: 3 chiamate LLM consecutive su fallimento deterministico | `ingest-queue.ts:805-820` | backoff esponenziale + classificazione errori non-retriable |
| B-6 | 🔵 | **Sweep partial-batch**: bail mid-loop non rolla back item già risolti | `sweep-reviews.ts:365` | snapshot+commit atomico o accettare (documentare) |
| B-7 | 🔵 | **`overview.md` overwrite**: riscritta integralmente ogni ingest, drift se il modello omette topic | `ingest.ts:2024` | merge-guard o diff-append |
| B-8 | 🔵 | **Regenerate `setTimeout(50)`**: hack di timing invece di await stato | `chat-panel.tsx:340` | await esplicito sullo store |

---

## 5. Performance

### P-1 🟠 Alto — Rebuild totale del grafo ad ogni `dataVersion` — *effort L*
`buildWikiGraph` ri-legge **ogni** `.md`, ri-esegue Louvain e ricalcola la relevance di ogni arco a ogni cambiamento (`graph-view.tsx:742-746` → `wiki-graph.ts`). Nessun update incrementale. Su vault da centinaia di pagine è il costo dominante e ri-blocca la UI.
**Fix**: indice incrementale (aggiornare solo i nodi/archi delle pagine cambiate); cache cohesion/community tra rebuild. *(Direttamente rilevante per l'invariante Synapse-I1 e I2.)*

### P-2 🟠 Alto — ForceAtlas2 sul main thread sotto 220 nodi — *effort M*
Worker solo per `≥220` nodi (`graph-view.tsx:83`); per `1<n<220` `runMainThreadLayout()` gira sincrono fino a 140 iterazioni (`:374-399`) → freeze percepibile su grafi piccoli/medi, proprio il caso d'uso più comune al day-one.
**Fix**: abbassare la soglia worker a ~50 nodi o spostare *sempre* FA2 nel worker. *(Rilevante per Synapse-I2: "NEVER run a force layout on the UI main thread".)*

### P-3 🟡 Medio — Allocazioni ripetute nel calcolo relevance — *effort S*
`getNeighbors` ricostruisce un `Set` ad ogni chiamata (`graph-relevance.ts:140-145`), `calculateRelevance` fa `new Set(nodeA.sources)` per coppia (`:260`). O(allocazioni) per arco per build.

### P-4 🔵 Basso — Letture file intere in RAM nei parser — *effort M*
DOCX/PPTX/XLSX e MinerU (base64) leggono l'intero file in memoria (`fs.rs:449`, `mineru.ts:678`); cap 200MB bonderizza ma è un rischio su input patologici. Nessuno streaming.

---

## 6. Test coverage & gate di qualità

### Q-1 🔴 Critico — La CI NON esegue i test — *effort S*
`ci.yml` fa solo `npx vite build` + `cargo build` su 3 OS. **Nessun `npm test`, nessun `cargo test`**, nonostante esistano 121 file di test TS e ~190 funzioni di test Rust. I test esistono ma **non presidiano nulla** in PR: una regressione passa il merge.
**Fix (alto ROI, basso effort)**: aggiungere step `npm run test:mocks` + `cargo test` al job CI. (I `real-llm` restano gated da `RUN_LLM_TESTS=1`.)

### Q-2 🟠 Alto — Zero test del layer UI, nessun E2E — *effort L*
Dei 52 componenti `.tsx`, solo gli helper puri estratti sono testati; **nessun render-test** (no `@testing-library/react`/jsdom) e **nessun E2E** (no Playwright/tauri-driver). CodeMirror/Milkdown editor, sigma graph, chat panel, 3-panel layout, dialog: non verificati a livello di render. I contratti cross-process Tauri↔TS sono coperti solo via `invoke` mockato.
**Fix**: smoke E2E Playwright sui flussi critici (create project → import → ingest → chat → graph) + qualche render-test sui componenti ad alto rischio.

### Q-3 🟠 Alto — Nessun linter configurato — *effort S*
Nessun file ESLint/Prettier nel repo; `package.json` non ha né `eslint` né `prettier` in devDependencies. Solo `tsc` per il typecheck.
**Fix**: aggiungere ESLint (typescript-eslint) + Prettier + `cargo clippy`/`cargo fmt --check` in CI.

**Positivi test (preservare)**: il layer `lib/`+`stores/` è testato a fondo — unit + **5 suite property-based `fast-check`** + integration + scenario + 10 suite `real-llm` (gated). Le race difficili sono presidiate: `sweep-reviews.race.test.ts` (switch progetto + abort), `project-mutex.test.ts`, `ingest-queue.integration.test.ts`. I test asseriscono le costanti esatte (`JUDGE_BATCH_SIZE=40`, budget). Rust testa il security-relevant (CORS deny, `safe_join` traversal, bind sanitization, proxy redaction). **Gap mirati**: nessun test su `graph-relevance`/`graph-insights`/`wiki-graph` (i pesi F4 non sono presidiati) e su `clip_server.rs` (la superficie più debole).

---

## 7. UI / UX review (da hacker dell'interfaccia)

> Premessa: senza poter eseguire la build qui, la review UX è **statica** (lettura componenti, i18n, flussi). Dove serve verifica visiva è segnalato.

### UX-1 🟠 Alto — Onboarding "Quick Start" con frizione sulla configurazione provider — *effort M*
Il flusso dichiarato (crea progetto → Settings → import → ingest) ha un punto cieco: **senza un LLM configurato, l'ingest non parte** (`clip-watcher.ts:39 hasUsableLlm`, `enqueueIngest` no-op) ma — dai percorsi letti — il blocco è gestito a valle, non con un **gate guidato pre-import**. `has-usable-llm.ts` esiste ed è testato: va **promosso a empty-state bloccante** ("Configura un provider per iniziare") sulla Sources view, non lasciato come check silenzioso.
**Verificare**: esiste un empty-state esplicito quando `!hasUsableLlm` sulla Sources/Chat? In caso negativo, l'utente importa, "non succede nulla", e non sa perché.

### UX-2 🟡 Medio — Densità informativa del Knowledge Graph senza guida — *effort M*
Il grafo ha legenda type/community, hover-highlight, insights — ma il README stesso promette feedback (label di relevance su hover archi) **che non esiste** (`renderEdgeLabels:false`). Un nuovo utente non ha modo di leggere *perché* due nodi sono connessi. Aggiungere il tooltip di relevance score mantenuto dal README chiuderebbe il gap doc↔UX (effort S).

### UX-3 🟡 Medio — Stati di loading/errore dell'ingest a due step — *effort S*
L'Activity Panel mostra progress/stato (buono), ma gli errori di streaming LLM in ingest diventano `status:"error"` sull'activity item e poi `throw` (`ingest.ts:986-989`): **verificare** che il messaggio d'errore sia *leggibile e azionabile* nel pannello (retry visibile c'è; il *testo* dell'errore è chiaro?). I fallimenti di auto-ingest del Deep Research sono invece **invisibili** (B-2).

### UX-4 🟡 Medio — Accessibilità probabilmente debole — *effort M (audit) + L (fix)*
La sidebar usa `Tooltip` su icone (`icon-sidebar.tsx`) ma servono verifiche: focus order, `aria-label` su pulsanti icona-only, contrasto del tema (slate su slate negli archi è di per sé basso contrasto), navigazione tastiera nei pannelli resizable, ruoli ARIA su dialog/review. `keyboard-utils.ts` esiste (segnale positivo) ma non sostituisce un audit. **Da verificare con axe/Playwright.**

### UX-5 🔵 Basso — Coerenza design system shadcn/ui — *effort S*
`components/ui/` ha i primitivi shadcn (button/dialog/input/label/resizable/scroll-area/separator/tooltip). Il resto dell'app li riusa, ma componenti complessi (graph, chat, review) reinventano molto styling ad-hoc inline. Coerenza accettabile, ma `graph-view.tsx` (monolite) è il punto dove diverge di più.

### UX-6 🔵 Basso — Confronto con Obsidian (editor Milkdown) — *effort N/A (decisione)*
Avendo scelto di **sostituire** Obsidian (non affiancarlo, vedi cross-check Karpathy in doc 01), l'editor Milkdown WYSIWYG deve reggere il confronto con l'editing Obsidian. Milkdown 7.20 è solido per markdown+math, ma WYSIWYG su `[[wikilink]]` e graph-view non eguaglia Obsidian nativo. **Decisione di prodotto**, non bug: per una "v1.0 usabile day-one" l'asticella è alta perché il README dichiara compatibilità Obsidian — l'utente *confronterà*.

**Positivi UX**: la gestione dell'update-dot vs banner è ben pensata (`icon-sidebar.tsx:42-49`); il dialog research è editabile prima del lancio (buona affordance); il roll 5-righe del thinking con fade è un tocco curato; i template-scenario riducono il cold-start.

---

## 8. Debito tecnico — sintesi prioritaria

| Priorità | Tema | Item |
|---|---|---|
| **P0 (rompe sicurezza)** | Clip server | S-1, S-2, S-3, S-4, S-5 |
| **P0 (rompe il gate qualità)** | CI/test | Q-1 (CI esegua i test), Q-3 (linter) |
| **P1 (blocca usabilità day-one)** | UX + perf | UX-1 (gate provider), P-2 (FA2 main-thread), P-1 (rebuild grafo), B-2 (errori silenziosi) |
| **P1 (doc↔codice)** | Verità | Q-5: allineare README ai numeri reali (budget, +10, 2-hop, type-affinity, cosine, port, timeout, pdf-extract) — *o* il codice al README |
| **P2 (rimandabile a v1.1+)** | Manutenibilità | A-1 (split graph-view), A-2 (normalizePath Rust), Q-TS-2/3 (dedup + YAML parser), P-4 (streaming parser) |

I dettagli operativi — chi fa cosa, in che ordine, con quali criteri di accettazione — sono in **`03-PIANO-AGENTICO-v1.0.md`**.
