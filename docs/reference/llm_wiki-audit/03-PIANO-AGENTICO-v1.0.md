# LLM Wiki — Piano agentico v0.5.4 → v1.0

> Piano d'azione strutturato per **Claude Code in modalità agentica** (multi-step, con verifica autonoma ad ogni step) sul repo `nashsu/llm_wiki`.
> **Obiettivo dichiarato**: portare il progetto da v0.5.4 a una **v1.0 usabile day-one**, **senza peggiorare** l'intuitività di interfaccia e funzionalità — semmai consolidandola.
> Basato sui findings di `01-AUDIT-FUNZIONALE.md` e `02-CODE-UI-REVIEW.md`. Tutti i criteri di accettazione sono **verificabili** (comando/asserzione), non soggettivi.

---

## 0. Principi guida del piano

1. **Priorità = (a) ciò che rompe la promessa del README · (b) ciò che blocca l'usabilità reale day-one · (c) debito tecnico rimandabile a v1.1+.** Sicurezza e gate di qualità vengono prima di tutto.
2. **Verità prima di feature.** Dove README e codice divergono, la v1.0 deve riconciliarli (preferendo *fixare la doc* quando il comportamento del codice è corretto, *fixare il codice* quando è il README a descrivere l'intento giusto).
3. **Non riprogettare ciò che funziona.** Vedi §"Zona rossa — NON toccare". Si *hardena*, non si riscrive.
4. **Ogni task è atomico ed eseguibile da un subagent** con un criterio di accettazione automatizzabile. Nessun task "migliora X".
5. **Ogni sprint chiude con i test verdi in CI** (che a fine Sprint 0 *eseguirà davvero* i test).

### Zona rossa — NON toccare (rischio regressione alto, valore già consolidato)

| Sottosistema | Perché non toccarlo | File |
|---|---|---|
| **Two-step CoT ingest** | Funziona, testato (`ingest.prompt.test.ts`, scenari). Solo hardening (path purpose, backoff). | `ingest.ts` |
| **Cascade delete** | 3-method + shared-entity, 87 casi di test. | `wiki-cleanup.ts`, `source-lifecycle.ts`, `wiki-page-delete.ts` |
| **Pesi grafo 4-segnali** | Esatti e corretti (`3/4/1.5/1`). Aggiungere *test*, non cambiarli. | `graph-relevance.ts:30-43` |
| **RRF / search Rust** | Tokenizer CJK + RRF testati. | `search.rs`, `search-rrf.ts` |
| **Hardening API `:19828`** | Già robusto (timing-safe, fail-closed, safe_join). Replicare sul clip server, non indebolire. | `api_server.rs` |
| **Sweep-reviews race-fix** | Già fixato e presidiato da test. Non rifattorizzare il guard pattern. | `sweep-reviews.ts` |
| **Persistenza chat/queue/review** | Formati su disco stabili; cambiarli rompe i dati utente. | `persist.ts`, `ingest-queue.ts`, `review-store.ts` |

---

## 1. Struttura subagent consigliata

Modello multi-agent (stile Synapse). Ogni subagent ha responsabilità, file di competenza e un `CLAUDE.md` di contesto da creare nel repo `llm_wiki` (sezione §8).

| Subagent | Responsabilità | Competenza file | Modello suggerito |
|---|---|---|---|
| **orchestrator** (main) | Guida gli sprint, fa merge dei risultati, gate di accettazione | — | opus |
| **rust-security-engineer** | Clip server, API server, bind/CORS, safe_join, panic-safety | `src-tauri/src/{clip_server,api_server,cors,server_bind,proxy}.rs` | opus (sicurezza) |
| **rust-backend-engineer** | Parser multi-format, file_sync, vectorstore, normalizePath Rust | `src-tauri/src/commands/*.rs` | sonnet |
| **frontend-ux-engineer** | Onboarding/empty-states, accessibilità, split `graph-view`, FA2 worker, perf grafo | `src/components/**`, `src/stores/**` | sonnet |
| **ingest-pipeline-engineer** | Hardening ingest (purpose path, backoff, overview merge, cache read-side), deep-research dispatch | `src/lib/ingest*.ts`, `deep-research.ts`, `scheduled-import.ts` | opus (loop/F17-adiacente) |
| **qa-test-engineer** | CI gate, test UI/E2E Playwright, test mancanti grafo/clip, linter | `.github/workflows/*`, `**/*.test.ts(x)`, nuova config eslint | sonnet |
| **docs-truth-engineer** | Riconciliare README (tutte le lingue) col codice; ADR delle scelte | `README*.md`, nuova `docs/adr/` | sonnet |
| **release-engineer** | Versioning, changelog, build matrix, signing, smoke di release | `.github/workflows/build.yml`, `tauri.conf.json`, `src/lib/changelog.ts` | haiku |

> Regola di confine: `rust-security-engineer` possiede i server HTTP; nessun altro subagent modifica `clip_server.rs`/`api_server.rs`. `frontend-ux-engineer` non tocca la pipeline ingest (è di `ingest-pipeline-engineer`). Conflitti su `graph-view.tsx` → coordinati dall'orchestrator (è competenza `frontend-ux`, ma `qa` ne aggiunge i test).

---

## 2. Sprint 0 — Stabilizzazione & fix critici (sicurezza + gate qualità)

**Obiettivo**: chiudere i 4 critici e mettere in piedi il gate di qualità *prima* di qualsiasi altra modifica, così che ogni sprint successivo sia presidiato.

| Task | Owner | Descrizione | Accettazione (verificabile) | Dip. |
|---|---|---|---|---|
| **S0-1** | qa | CI esegue i test | Aggiungere a `ci.yml` step `npm run test:mocks` e `cargo test --manifest-path src-tauri/Cargo.toml` | Il job CI fallisce se un test fallisce; PR di prova con test rotto → CI rossa | — |
| **S0-2** | qa | Linter + format in CI | ESLint (typescript-eslint) + Prettier + `cargo clippy -- -D warnings` + `cargo fmt --check` | `npm run lint` e `cargo clippy` esistono e girano in CI; 0 error sul codice attuale (warning ammessi inizialmente) | — |
| **S0-3** | rust-security | Token-gate sul clip server | Clip server richiede lo **stesso** token dell'API `:19828` (header/Bearer), confronto `constant_time_eq`; l'estensione riceve il token via handshake | Test Rust: richiesta senza token → 401; con token valido → 200. `grep -i 'is_authorized' clip_server.rs` non vuoto | S0-1 |
| **S0-4** | rust-security | `safe_join` sul path del clip | `handle_clip` valida `project_path` contro i progetti registrati e applica `safe_join` | Test: `projectPath` fuori dai progetti registrati → 400/403; path-traversal `..` → rifiutato | S0-3 |
| **S0-5** | rust-security | Origin allowlist + body cap clip | Validare `Origin`/`Host` (allowlist `chrome-extension://<id>` + `127.0.0.1`); `read_to_string` → `.take(MAX_BODY_BYTES)` | Test: POST con Origin arbitrario → rifiutata; body > cap → 413 | S0-3 |
| **S0-6** | rust-security | Niente clip server unauth su LAN | Se `allowLanAccess` e clip server attivo, **richiedere** token (no bind `0.0.0.0` senza auth); warning di startup se `allowUnauthenticated && allowLanAccess` | Test: config LAN+no-token → clip server resta `127.0.0.1` o rifiuta; log di warning presente | S0-3, S0-4 |
| **S0-7** | qa | Test sui pesi grafo (regressione) | Unit test che asserisce `WEIGHTS = {3.0,4.0,1.5,1.0}` e un caso noto di `calculateRelevance` | `graph-relevance.test.ts` esiste e verde; mutare un peso → test rosso | S0-1 |

**Criterio di uscita Sprint 0**: CI verde **che esegue i test**; clip server autenticato + path-safe + LAN-safe con test dedicati; nessun critico aperto. **Checkpoint umano** prima di Sprint 1.

---

## 3. Sprint 1 — Completamento feature parziali & verità doc↔codice

**Obiettivo**: chiudere il gap tra ciò che il README promette e ciò che il codice fa — completando il poco che manca e correggendo la documentazione dove il codice è già giusto.

| Task | Owner | Descrizione | Accettazione | Dip. |
|---|---|---|---|---|
| **S1-1** | ingest | Fix purpose path divergence (B-3) | Unificare entrambi gli entry-point su root `purpose.md` | Test: `startIngest` e `autoIngest` leggono lo stesso file; un purpose non vuoto compare nel prompt di entrambi | S0 |
| **S1-2** | ingest | Implementare F2 "suggerimento update purpose.md" *oppure* declassare il claim | Opzione A (preferita): a fine ingest il LLM può emettere un blocco `---PURPOSE-SUGGESTION---` che finisce nella Review queue come item dedicato (azione: applica/ignora). Opzione B: rimuovere il claim dal README. | Se A: test che un'analysis con drift di scope genera un review item `purpose-suggestion`; l'utente lo applica → `purpose.md` aggiornato. Se B: README non menziona più la feature | S0 |
| **S1-3** | docs-truth | Riconciliare i numeri del README (tutte le lingue) | Correggere: budget **50/5/15/30** (non 60/20/5/15); rimuovere "+10 title bonus" (reale 5/50/200); "1-hop, no decay" (non 2-hop+decay); type-affinity = *cross-type bonus* (non same-type); "L2-derived score" (non cosine); search solo `wiki/` (non raw/sources); port 19827(clip)/19828(API); timeout 30 min; PDF via `pdfium-render` | Diff su README.md/CN/JA/KO; un check automatico (`scripts/check-readme-claims`) confronta le costanti citate con `context-budget.ts`/`search.rs`/`llm-client.ts` | S0-1 |
| **S1-4** | frontend-ux | Allineare label budget UI (B/UX) | `context-size-selector.tsx:39` mostra il valore reale per il wiki (55%, = 50% pagine + 5% index), non 60% | Il testo riflette `PAGE_BUDGET_FRAC+INDEX_BUDGET_FRAC`; snapshot test | S1-3 |
| **S1-5** | frontend-ux | Tooltip relevance score su hover archi (UX-2) | Implementare la label di relevance promessa dal README (o decidere via ADR di rimuoverla dal README in S1-3) | Su hover di un arco compare lo score; test di rendering del reducer | S1-3 |
| **S1-6** | ingest | Verificare/wire la SHA256 cache read-side (F3-bis) | Confermare i caller di `checkIngestCache` nel path coda; se assente, wire lo skip-unchanged | Test: ingest di un file invariato due volte → seconda volta skip senza chiamata LLM (mock conta 0 chiamate) | S0 |

**Criterio di uscita Sprint 1**: nessuna feature 🟡 "parziale" o "vaporware" resta tale (o completata o doc-corretta); `check-readme-claims` verde. **Checkpoint umano**.

---

## 4. Sprint 2 — Hardening error handling & robustezza

**Obiettivo**: rendere i fallimenti *visibili e recuperabili*; eliminare i comportamenti silenziosi e le race residue.

| Task | Owner | Descrizione | Accettazione | Dip. |
|---|---|---|---|---|
| **S2-1** | ingest | Backoff sui retry di ingest (B-5) | Backoff esponenziale tra retry; classificare errori non-retriable (4xx auth, parse) per non sprecare i 3 tentativi | Test: errore deterministico → ritardo crescente tra tentativi; errore non-retriable → 0 retry | S0 |
| **S2-2** | ingest | Surfacing auto-ingest Deep Research (B-2) | Fallimento auto-ingest → stato task `ingest-failed` visibile + retry | Test: autoIngest che rigetta → task non resta `done`; UI mostra errore | S0 |
| **S2-3** | ingest | Fix double-dispatch research (B-1) | Marcare il task non-`queued` **sincronicamente** prima del lancio async | Test con `available≥2` e 2 task in coda → ogni task lanciato **una** sola volta | S0-1 |
| **S2-4** | ingest | `scanning` per-progetto (B-4) | Map `scanning` keyed per projectId | Test: scan di P1 in volo non droppa scan di P2 | S0 |
| **S2-5** | rust-backend | Unificare `normalizePath` Rust (A-2) | Un solo helper con semantica documentata (separatori; trailing-slash e case-fold come opzioni esplicite) | I tre call-site usano l'helper; test del caso trailing-slash | S0 |
| **S2-6** | ingest | Merge-guard `overview.md` (B-7) | Evitare overwrite distruttivo: append/merge o diff che preserva topic pregressi | Test: overview con topic A; ingest che non menziona A → A preservato | S0 |
| **S2-7** | rust-security | Test del clip server (gap) | Suite Rust per `clip_server.rs` (auth, safe_join, body cap, origin) | `cargo test clip_server` copre i 4 path; coverage del file > 0 | S0-3..S0-6 |

**Criterio di uscita Sprint 2**: nessun fallimento silenzioso nei path ingest/research; race B-1/B-4 chiuse con test; clip server testato. **Checkpoint umano**.

---

## 5. Sprint 3 — UX polish & onboarding (intuitività day-one)

**Obiettivo**: garantire che un nuovo utente arrivi da "app aperta" a "wiki popolato" **senza frizione né vicoli ciechi**. Questo è lo sprint che *consolida* l'intuitività LLM Wiki, non la peggiora.

| Task | Owner | Descrizione | Accettazione | Dip. |
|---|---|---|---|---|
| **S3-1** | frontend-ux | Gate provider guidato (UX-1) | Empty-state bloccante su Sources/Chat quando `!hasUsableLlm`: "Configura un provider" con CTA diretta a Settings | E2E: nuovo progetto senza provider → import mostra empty-state con link; nessun "import che non fa nulla" | S0 |
| **S3-2** | frontend-ux | Empty-states su tutte le view | Wiki/Graph/Review/Search/Research vuoti mostrano stato esplicito + prossima azione | E2E: ogni view a progetto vuoto rende un empty-state non-bianco | S0 |
| **S3-3** | frontend-ux | Validazione pre-ingest | Prima dell'ingest: check provider + formato file supportato + dimensione; messaggi chiari | E2E: import di formato non supportato → messaggio, non errore silenzioso | S3-1 |
| **S3-4** | frontend-ux | Audit accessibilità (UX-4) | `aria-label` su icon-button, focus order, ruoli dialog/review, contrasto archi grafo | `axe` (via Playwright) → 0 violazioni critiche/serie sulle view principali | S0-1 |
| **S3-5** | frontend-ux | Errori ingest leggibili nell'Activity Panel (UX-3) | Il testo dell'errore di streaming è mostrato e azionabile (retry) | E2E: ingest con LLM che erra → messaggio leggibile + retry funzionante | S2-2 |
| **S3-6** | frontend-ux | Onboarding "Quick Start" coerente | Verificare che il flusso README (crea→settings→import→ingest→chat→graph) sia percorribile senza dead-end | E2E end-to-end del Quick Start verde (con LLM mock) | S3-1..S3-3 |

**Criterio di uscita Sprint 3**: E2E del Quick Start verde; 0 violazioni a11y critiche; nessun dead-end per il nuovo utente. **Checkpoint umano** (review visiva).

---

## 6. Sprint 4 — Performance & test coverage

**Obiettivo**: rispettare le promesse di fluidità (grafo) e portare la copertura sul layer finora scoperto.

| Task | Owner | Descrizione | Accettazione | Dip. |
|---|---|---|---|---|
| **S4-1** | frontend-ux | FA2 sempre fuori dal main thread (P-2) | Abbassare la soglia worker a ~50 nodi (o sempre worker) | Benchmark: layout di 150 nodi non blocca il main thread > 50ms (misura `performance`) | S0 |
| **S4-2** | frontend-ux | Update incrementale del grafo (P-1) | Aggiornare solo nodi/archi delle pagine cambiate su `dataVersion`; cache community/cohesion | Benchmark: ingest di 1 pagina su vault da 300 → rebuild < X ms (vs full); test che il delta sia corretto | S4-1 |
| **S4-3** | frontend-ux | Ridurre allocazioni relevance (P-3) | Memoizzare `getNeighbors`/`Set(sources)` per build | Microbench: nessuna allocazione per-coppia ridondante; test invariato | S4-2 |
| **S4-4** | qa | Smoke E2E Playwright (Q-2) | Suite E2E sui flussi critici: create→import→ingest→chat→graph→review→delete | CI esegue Playwright headless; suite verde | S0-1, S3 |
| **S4-5** | qa | Render-test componenti ad alto rischio | `@testing-library/react` su chat-panel, review-view, settings-view, file-tree | Test di render verdi per i 4 componenti | S0-1 |
| **S4-6** | qa | Test su `graph-insights` / `wiki-graph` | Coprire Louvain/cohesion/insights con casi noti | `graph-insights.test.ts`, `wiki-graph.test.ts` verdi | S0-1 |
| **S4-7** | rust-backend | Guard memoria parser (P-4) | Cap espliciti e/o streaming dove fattibile su DOCX/PPTX/XLSX | Test: file oltre soglia → errore controllato, non OOM | S0 |

**Criterio di uscita Sprint 4**: benchmark grafo entro target; E2E + render-test in CI; coverage del layer grafo e UI non più a zero. **Checkpoint umano**.

---

## 7. Sprint 5 — Release readiness v1.0

**Obiettivo**: rendere il progetto *shippabile* come v1.0 con fiducia.

| Task | Owner | Descrizione | Accettazione | Dip. |
|---|---|---|---|---|
| **S5-1** | docs-truth | ADR delle decisioni chiave | `docs/adr/` con: scelta sicurezza clip server, split budget, sostituzione Obsidian, divergenze Karpathy | Almeno 4 ADR; indice linkato dal README | S1-3 |
| **S5-2** | qa | Matrice smoke 3-provider | Smoke ingest+chat su almeno OpenAI-compat + Anthropic + Ollama (mock o `RUN_LLM_TESTS`) | Suite smoke verde sui 3 path provider | S4 |
| **S5-3** | release | Build matrix + artefatti firmati | `build.yml` produce dmg/deb/AppImage/msi verificati; changelog generato | Release di prova: tutti gli artefatti buildano; `changelog.ts` riflette i cambiamenti | S4 |
| **S5-4** | release | Bump versione 1.0.0 coerente | `package.json` + `tauri.conf.json` + Cargo allineati a `1.0.0` | `grep '1.0.0'` nei 3 file; CI di build verde | S5-3 |
| **S5-5** | docs-truth | Verifica finale README↔codice | Ri-eseguire `check-readme-claims`; aggiornare screenshot/asset se UI cambiata | Check verde; nessun claim non supportato dal codice | S1-3, S3 |
| **S5-6** | qa | Regressione completa | Tutta la suite (unit+property+integration+E2E) verde su 3 OS | CI completa verde su macOS/Ubuntu/Windows | tutti |

**Criterio di uscita Sprint 5**: tutti i gate verdi su 3 OS; artefatti firmati; doc allineata; versione 1.0.0. **Checkpoint umano finale → tag v1.0.0**.

---

## 8. CLAUDE.md di riferimento da creare nel repo `llm_wiki`

Per orchestrare i subagent serve un file di contesto unico (analogo a quello di Synapse). Creare `llm_wiki/CLAUDE.md` con:

1. **Invarianti del progetto** (estratti da questo audit), es.:
   - *I-SEC*: nessun server HTTP locale può scrivere su filesystem senza autenticazione + `safe_join`.
   - *I-PERF*: nessun force-layout sul main thread; il grafo non si ricostruisce mai interamente quando cambia una sola pagina.
   - *I-TRUTH*: ogni numero nel README deve corrispondere a una costante nel codice (presidiato da `check-readme-claims`).
   - *I-GATE*: nessun merge senza test verdi in CI + linter pulito.
   - *I-OBSIDIAN*: `wiki/` resta un vault Obsidian valido (frontmatter, `[[wikilink]]`, `.obsidian/`).
2. **Zona rossa** (§1 di questo doc) — i sottosistemi da hardenare ma non riscrivere.
3. **Mappa file→owner** (la tabella subagent di §1).
4. **Costanti autoritative** con path: `graph-relevance.ts:30-43`, `search.rs:14-21`, `context-budget.ts:54-59`, `llm-client.ts:96`, `clip_server.rs:17`, `api_server.rs:19`.
5. **Convenzioni commit**: `feat|fix|sec|docs|test(scope): descr (#sprint-task)`.

Inoltre, un file per-subagent in `llm_wiki/.claude/agents/` (es. `rust-security-engineer.md`) con scope-confini e file di competenza, così che l'orchestrator possa lanciarli in parallelo senza conflitti di edit.

---

## 9. Dipendenze tra sprint (ordine di esecuzione)

```
Sprint 0 (sicurezza + gate)         ──┐  [BLOCCANTE per tutti]
                                       ▼
Sprint 1 (parziali + verità doc) ──┬──► Sprint 2 (error handling)
                                   │        │
                                   ▼        ▼
                              Sprint 3 (UX/onboarding) ──► Sprint 4 (perf + test)
                                                                │
                                                                ▼
                                                         Sprint 5 (release)
```

- **Sprint 0 è bloccante**: nessun altro lavoro parte prima che la CI esegua i test e i critici di sicurezza siano chiusi (altrimenti si costruisce su un gate inesistente).
- Sprint 1 e 2 possono parzialmente sovrapporsi (owner diversi: docs-truth vs ingest).
- Sprint 3 dipende da Sprint 1 (gate provider richiede la verità su `hasUsableLlm`) e alimenta gli E2E di Sprint 4.
- Sprint 5 dipende da tutto.

---

## 10. Definition of Done v1.0 (checklist verificabile punto-per-punto)

**Sicurezza**
- [ ] Clip server `:19827` richiede token; richiesta unauth → 401 (test Rust).
- [ ] `handle_clip` applica `safe_join` + valida `project_path` contro i progetti registrati (test path-traversal).
- [ ] Clip server: Origin allowlist + body cap (test).
- [ ] Nessun server HTTP bind su `0.0.0.0` senza autenticazione; warning di startup sulla combo unauth+LAN (test).
- [ ] `?token=` in querystring deprecato o gated.

**Gate di qualità**
- [ ] CI esegue `npm run test:mocks` **e** `cargo test`; una regressione fa fallire la PR.
- [ ] ESLint + Prettier + `cargo clippy -D warnings` + `cargo fmt --check` in CI, verdi.
- [ ] Suite E2E Playwright headless in CI, verde su macOS/Ubuntu/Windows.
- [ ] Render-test sui 4 componenti ad alto rischio; test su `graph-relevance`/`graph-insights`/`wiki-graph`/`clip_server`.

**Verità doc↔codice**
- [ ] `check-readme-claims` verde: budget 50/5/15/30, niente "+10", "1-hop no decay", type-affinity cross-type, score L2, port 19827/19828, timeout 30 min, `pdfium-render` — coerenti in README.md/CN/JA/KO.
- [ ] Almeno 4 ADR in `docs/adr/`.

**Feature**
- [ ] F2: o "suggerimento purpose" implementato (review item `purpose-suggestion`, test) o claim rimosso dal README.
- [ ] Purpose path unificato a root (test su entrambi gli entry-point).
- [ ] SHA256 cache read-side verificata (test skip-unchanged a 0 chiamate LLM).
- [ ] Deep Research: no double-dispatch (test `available≥2`); auto-ingest failure visibile (test).

**UX / onboarding**
- [ ] E2E Quick Start (crea→settings→import→ingest→chat→graph) verde, senza dead-end.
- [ ] Empty-state bloccante quando `!hasUsableLlm`; empty-states su tutte le view principali.
- [ ] Validazione pre-ingest (provider + formato + dimensione) con messaggi chiari.
- [ ] `axe`: 0 violazioni critiche/serie sulle view principali.
- [ ] Errori di streaming ingest leggibili + retry nell'Activity Panel.

**Performance**
- [ ] FA2 mai sul main thread per grafi tipici (>50ms di blocco vietato a 150 nodi — benchmark).
- [ ] Grafo: update incrementale su `dataVersion` (rebuild parziale verificato).
- [ ] Parser multi-format con guard di memoria (test su file oltre soglia).

**Release**
- [ ] Versione `1.0.0` allineata in `package.json` / `tauri.conf.json` / `Cargo.toml`.
- [ ] Build matrix produce dmg/deb/AppImage/msi; smoke 3-provider verde.
- [ ] Regressione completa verde su 3 OS.
- [ ] `wiki/` resta vault Obsidian valido (frontmatter + `[[wikilink]]` + `.obsidian/` — test di compatibilità).

> **Nota finale**: la v1.0 così definita **non aggiunge feature nuove** rispetto al ricco set v0.5.4 — *consolida* ciò che c'è (sicurezza, affidabilità, verità, fluidità, onboarding). È esattamente l'obiettivo: una v1.0 *usabile day-one* senza peggiorare l'intuitività che è il punto di forza di LLM Wiki.
