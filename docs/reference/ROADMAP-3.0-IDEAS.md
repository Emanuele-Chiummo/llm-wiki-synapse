# Synapse 3.0 — quaderno di idee

> **Stato: pensatoio, non un impegno.** Nessuna di queste voci è approvata, stimata
> seriamente o schedulata. Serve a far sedimentare le idee mentre la 2.1.x continua a
> ricevere manutenzione settimanale.
>
> **Come si alimenta:** (a) la routine di manutenzione settimanale
> ([runbook](../process/WEEKLY-MAINTENANCE.md) §6) ci scarica tutto ciò che incontra ed è
> troppo grosso per una patch; (b) Emanuele ci butta dentro idee quando gli vengono.
>
> **Cos'è una "3.0":** un major SemVer autorizza **breaking change**. Se un'idea non
> richiede di rompere qualcosa (API, schema, contratto d'uso) o non è un salto di
> categoria del prodotto, probabilmente è una 2.2, non una 3.0.
>
> Aperto: 2026-08-07, subito dopo la 2.1.7.

---

## Dove siamo (baseline 2.1.7)

Ciò che è **fatto e solido**, e che quindi *non* va rimesso in discussione a cuor leggero:

- Parità funzionale con `nashsu/llm_wiki` chiusa (K1–K8, F1–F18).
- Invarianti I1–I9 rispettati e verificati in CI (incrementalità, layout server-side,
  no-work-per-token, CodeMirror, compatibilità Obsidian, provider pluggable, loop bounded,
  docs-as-DoD, no-reinvent).
- Suite test ampia e veloce: ~3160 pytest + ~2550 vitest + E2E Playwright su Postgres reale.
- Superficie di distribuzione larga: web/PWA, desktop Tauri (macOS + Windows) con updater,
  clipper Chrome, MCP remoto (bearer + **OAuth 2.1/PKCE** dalla 2.1.6), app iOS nativa in
  costruzione.
- Sicurezza: Fernet at-rest, PBKDF2 sui token, CSP, rate-limit, Cloudflare Access, token
  scoped revocabili.

Il prodotto **funziona**. Una 3.0 non serve a farlo funzionare: serve a cambiare *cosa è*.

---

## Tema 1 — Ingest deterministico e riparabile ⭐

**Il problema più concreto che abbiamo.** Nella sola settimana del 20-22 luglio 2026 il
loop di ingest si è rotto per **quattro cause radice indipendenti**:

| Release | Causa |
|---|---|
| 2.1.2 | `rsplit(".", 1)` non spogliava le sottocartelle dal path sorgente |
| 2.1.4 | `max_thinking_tokens` non limitato → il turno si consumava in ragionamento, zero output |
| 2.1.5 | `index.md` illimitato nel system prompt → superato `ARG_MAX` del kernel (E2BIG) |
| 2.1.7 | `POST /mcp/server` senza slash finale → 307, connettore MCP morto |

Il pattern è chiaro: **la generazione a blocchi è un singolo turno di testo libero che deve
andare bene al primo colpo**, e ogni retry peggiora le cose (il prompt cresce con gli errori
precedenti invece di diventare più mirato). Quando fallisce, fallisce in modo silenzioso e
opaco: "Non convergito", nessun output parziale salvato, soldi spesi.

**Idee (breaking, quindi 3.0):**

- **Generazione per-pagina invece che monolitica.** Oggi un documento produce N pagine in
  un unico blob `---FILE:`. Se il blob è malformato, si perde *tutto*. Generare una pagina
  per chiamata: fallimenti isolati, retry mirato solo sulla pagina rotta, progresso
  parziale sempre salvato. Costa più chiamate, ma il costo di un run non convergente oggi è
  già ~0,50-2 $ buttati.
- **Output strutturato al posto del parsing di testo.** `---FILE:` / `---END FILE---` è un
  protocollo fragile inventato in casa. Tool-use / JSON Schema danno garanzia sintattica dal
  provider. Richiede però una strategia per Ollama (structured output via grammar) — I6 dice
  che il routing dev'essere capability-aware, non per-provider.
- **Contesto incrementale invece di dump.** `index.md` cresce di una riga per pagina e viene
  reincluso *per intero* in ogni prompt. In 2.1.5 l'ho tappato con un cap, ma è una pezza: la
  strada giusta è retrieval del contesto rilevante (le pagine *vicine* al documento in
  ingest) invece del catalogo completo.
- **Checkpoint e ripresa.** Un run interrotto (crash, budget, 429) dovrebbe riprendere da
  dove era, non ricominciare.

**Perché è il tema numero uno:** è il ciclo centrale del prodotto, è quello che l'utente vede
rompersi, ed è quello che brucia soldi quando sbaglia. Tutto il resto è cosmetica al confronto.

**Sforzo:** L. **Rischio:** alto (tocca il cuore). Serve l'harness di parità ADR-0083 vivo e
una fase di doppio-binario prima di spegnere il vecchio path.

---

## Tema 2 — Identità reale e multi-utente

Oggi Synapse è **mono-operatore per costruzione**: un `SYNAPSE_AUTH_TOKEN`, un vault per
backend, e — dalla 2.1.6 — un server OAuth il cui passo di consenso è approvato dallo *stesso*
token statico condiviso ([ADR-0090](../adr/0090-mcp-oauth-authorization-server.md) lo dice
esplicitamente: "se Synapse diventasse davvero multi-utente, servirebbe identità per-utente
dietro `/authorize`").

**Idee:**

- Tabella `users` + membership sui vault; ruoli (owner / editor / reader).
- Il consenso OAuth diventa un vero login utente, non un token condiviso.
- Vault condivisi in sola lettura → abilita il caso d'uso "pubblico il mio wiki a un collega".
- Audit log per-utente (chi ha ingerito cosa, chi ha approvato quale review).

**Attenzione:** questo è il classico cambiamento che *sembra* una feature e invece è una
riscrittura trasversale — tocca auth, MCP, clipper, iOS, ogni query che oggi assume
`settings.vault_id`. Da valutare solo se c'è un caso d'uso reale, non "perché sarebbe bello".

**Sforzo:** L. **Prerequisito** per: pubblicazione, collaborazione.

---

## Tema 3 — Qualità del recupero (retrieval)

Il retrieval a 4 fasi funziona, ma è fermo alla forma originale: vettoriale bge-m3 +
espansione sul grafo + budget + assemblaggio.

**Idee:**

- **Ibrido lessicale + vettoriale** (Postgres FTS / `pg_trgm` accanto a Qdrant). Era già
  stato scartato in v2.0 per priorità, ma resta il singolo miglioramento con miglior
  rapporto qualità/sforzo: i nomi propri e i codici (es. sigle normative) il vettoriale
  li sbaglia sistematicamente.
- **Reranking** del set candidato prima dell'assemblaggio.
- **Verifica delle citazioni:** oggi il modello cita `[n]` e ci fidiamo. Un passo di
  verifica automatica (la frase citata esiste davvero nella fonte?) alzerebbe molto la
  fiducia, che è *il* punto di un wiki.
- **Query understanding:** riformulazione/espansione prima del recupero.

**Sforzo:** M per l'ibrido, M per il reranking, L per la verifica citazioni.

---

## Tema 4 — Il grafo come strumento di ragionamento, non solo vista

Oggi il grafo è bellissimo e sostanzialmente **passivo**: lo guardi. I 4 segnali di
rilevanza e le community Louvain sono già calcolati e cachati server-side — cioè
l'infrastruttura c'è già, è sotto-sfruttata.

**Idee:**

- **Rilevamento di contraddizioni** attraversando il grafo (esiste già un abbozzo nel loop
  di consistenza post-ingest, mai portato a valore).
- **Grafo temporale:** com'è evoluta la conoscenza? Quali pagine sono state riscritte più
  volte? Dove la comprensione è cambiata?
- **Sintesi guidata dal grafo:** oggi synthesis/comparison nascono da cluster; potrebbero
  nascere da *buchi* strutturali (nodi ponte mancanti, community isolate).
- **Health del grafo come metrica di prodotto:** densità dei link, orfani, community
  frammentate — già in parte in Lint, ma non presentata come "salute del sapere".

**Sforzo:** M ciascuna. **Nota:** questo è il tema che differenzia di più Synapse da
"una cartella di markdown generata da un LLM".

---

## Tema 5 — iOS: da cantiere a prodotto

La Fase A è avviata ([ADR-0088](../adr/0088-ios-redesign-foundation.md): SwiftUI nativa,
design system allineato al brand, shell a 5 tab). Mancano le superfici vere e la
distribuzione.

**Idee:** Fase B (Home, lettura wiki, chat con streaming e citazioni, ricerca) e Fase C
(grafo, review queue, sorgenti, impostazioni) + TestFlight.

**Domanda aperta e non banale:** il grafo su iOS — WKWebView che riusa sigma.js (parità
garantita, I2 rispettato perché le coordinate arrivano già calcolate dal server) oppure
rendering nativo? L'ADR-0088 lascia la decisione **Proposed**, in attesa di misure vere su
device.

**Serve da te:** device fisico per i test di performance, iscrizione Apple Developer
Program (99 $/anno) per TestFlight.

**Sforzo:** L. **Candidato flagship** di una 3.0.

---

## Tema 6 — Economia: costo e velocità dell'inferenza

I7 impone budget e log dei costi, quindi i dati ci sono già. Non sono però mai stati usati
per *ottimizzare*.

**Idee:**

- **Prompt caching.** Schema, purpose e contesto del vault sono quasi identici tra un
  documento e il successivo. Su un import di 150 file è la leva di costo più grande
  disponibile, e oggi è completamente inutilizzata.
- **Batch API** per gli import massivi non interattivi (costo dimezzato, latenza
  irrilevante quando stai importando di notte).
- **Routing per complessità:** classificare la difficoltà del documento e mandare i casi
  facili a un modello più economico. Il tiering lo applichiamo già a *noi* durante lo
  sviluppo; non lo applichiamo al runtime del prodotto.
- **Dashboard costi con proiezione**, non solo consuntivo.

**Sforzo:** S/M ciascuna, e con ritorno immediato e misurabile. **Il tema meno affascinante
e forse più redditizio.**

---

## Tema 7 — Scala

Nessuno ha mai misurato Synapse su un vault grosso. Il benchmark a 5k pagine era stato
messo in lista e poi rinviato.

**Segnali che qualcosa scricchiola già:** `index.md` illimitato (2.1.5 lo dimostra),
FA2 ricalcolato da zero a ogni bump invece che a caldo, la home fa un fan-out di ~16
chiamate all'avvio, `HomeDashboard.tsx` è un monolite da ~3.100 righe mai scomposto.

**Idee:** benchmark onesto a 1k / 5k / 20k pagine con numeri pubblicati; FA2 warm-start;
paginazione/virtualizzazione ovunque manchi; `index.md` ripensato come vista materializzata
invece che file monolitico.

**Sforzo:** M per la misura (da fare *prima* di ottimizzare), poi dipende da cosa emerge.

---

## Tema 8 — Il wiki come qualcosa che si può mostrare

Oggi il wiki è visibile solo a chi ha accesso all'istanza.

**Idee:** publishing statico (export di un sito navigabile, ricerca inclusa), link pubblici
in sola lettura per singola pagina, export in PDF/EPUB di una sezione. Dipende dal Tema 2
se si vuole controllo d'accesso fine.

**Sforzo:** M. **Nota:** trasforma il prodotto da "strumento personale" a "strumento con
un pubblico" — decisione di prodotto, non tecnica.

---

## Tema 9 — Debito tecnico che vale una major

Roba rinviata più volte, che in una major si può affrontare rompendo qualcosa:

- `HomeDashboard.tsx` ~3.100 righe, mai scomposto (rinviato in 1.9.2 e in 2.0.0).
- `GraphViewer.tsx` ~1.565 righe dopo l'estrazione, obiettivo 800-1.000.
- 6 poller REST residui non ancora passati al canale SSI/SSE (il pattern è già dimostrato
  sui due consumatori principali).
- Migrazione completa dei client API all'envelope/`ApiError` condiviso (parziale).
- Lazy-load del locale i18n non attivo (rinviato tre volte: 1.9.3, 2.0.0, 2.1).
- Focus-trap mancante su `CascadeDeleteModal` — **questo gestisce cancellazioni**, ha
  priorità sugli altri due modali.
- Coverage ratchet strumentato ma senza soglia di gate.
- Harness di parità ADR-0083 mai rieseguito dopo la migrazione review-create al block loop.
- Lockfile delle dipendenze backend.

**Sforzo:** singolarmente S/M, complessivamente L. Nessuno di questi da solo giustifica una
major; insieme sono il "pulisci mentre hai il permesso di rompere".

---

## Come sceglierei (opinione, da discutere)

Se dovessi indicare **una** direzione: **Tema 1 (ingest deterministico)**, con il **Tema 6
(costi)** come compagno naturale — si toccano gli stessi file e il secondo ripaga il primo.

Il ragionamento: gli altri temi aggiungono superficie a un prodotto che ne ha già molta.
Il Tema 1 invece rende affidabile la cosa che l'utente fa *ogni giorno* e che oggi si rompe
in modo visibile e costoso. Quattro release di fila in una settimana per lo stesso ciclo non
sono sfortuna: sono un progetto che ti sta dicendo dove fa male.

Il **Tema 5 (iOS)** è il flagship più vistoso e il più facile da raccontare, ma è anche il
più isolato: non migliora nulla di ciò che esiste già.

Il **Tema 2 (multi-utente)** lo terrei fermo finché non c'è una seconda persona vera che
usa il sistema. È il tipo di complessità che, se introdotta "per il futuro", si paga ogni
giorno senza mai ripagarsi.

---

## Voci dalla manutenzione settimanale

<!-- La routine del venerdì aggiunge qui sotto. Formato:
### <titolo>
- **Problema:** ...
- **Evidenza:** file:riga
- **Impatto:** ...
- **Sforzo:** S/M/L
- **Trovato:** YYYY-MM-DD
-->

### pypdf fermo su 5.x con 24 advisory aperte — serve il salto a 6.x

- **Problema:** `pip-audit` riporta 24 advisory su `pypdf==5.9.0` (PYSEC-2026-3004…3027,
  PYSEC-2026-3610…3613, GHSA-jm82-fx9c-mx94). **Nessuna è corretta in una 5.x**: la fix più
  bassa è 6.7.1, la più alta 6.14.2. Il vincolo attuale `pypdf>=4.2,<6` esclude per
  costruzione ogni versione corretta, quindi il debito non si chiude con un bump patch.
- **Evidenza:** `backend/pyproject.toml:34` (`"pypdf>=4.2,<6"`),
  `backend/requirements-lock.txt:242` e `backend/requirements-prod-lock.txt:207`
  (`pypdf==5.9.0`).
- **Impatto:** pypdf è l'estrattore PDF **di default** (`app/config.py:866`,
  `pdf_extractor="pypdf"`) ed è anche il fallback incondizionato quando Marker o MinerU
  falliscono (`app/ingest/extract.py:26-27`) — quindi ogni PDF che entra nel vault, incluso
  materiale scaricato dal web via clipper o deep-research, passa da qui. Le advisory di questa
  famiglia sono tipicamente DoS su file malformati (loop infiniti, esaurimento memoria) più
  che RCE, e il deployment è mono-operatore: rischio reale ma contenuto. Non abbastanza
  contenuto da lasciarlo indefinito.
- **Sforzo:** M. Il salto 5.x → 6.x è un **major** con API breaking (la routine settimanale ha
  il divieto esplicito di major, §2 punto 6 del runbook): va allargato il vincolo in
  `pyproject.toml`, rigenerati entrambi i lockfile, e riverificato `app/ingest/extract.py`
  contro l'API 6.x, con un test su PDF reali.
- **Trovato:** 2026-08-07

### Nessuna superficie operatore per vedere e revocare le concessioni OAuth MCP

- **Problema:** dalla 2.1.8 un cambio del token statico revoca *tutte* le concessioni OAuth
  insieme, ma resta l'unico strumento disponibile: è un'accetta, non un bisturi. Non esiste
  modo di elencare i client autorizzati, vedere quando hanno ottenuto accesso, o revocarne
  **uno solo** senza buttare giù anche gli altri e senza cambiare il token che gli altri
  client (Claude Desktop) stanno usando.
- **Evidenza:** `backend/app/mcp/oauth.py` non espone alcuna rotta di lettura/revoca;
  `backend/app/routers/config/mcp.py` gestisce solo il token statico. Il modello
  `McpOAuthClient` / `McpOAuthToken` (`backend/app/models.py:2576`) ha già i campi necessari
  (`client_name`, `created_at`, `revoked_at`).
- **Impatto:** una volta che più di un client OAuth è configurato — plausibile appena si
  aggiungono claude.ai web e un secondo dispositivo — la revoca selettiva diventa necessaria.
  Oggi l'operatore non ha nemmeno visibilità su *quali* client abbiano accesso.
- **Sforzo:** M. Richiede endpoint REST (`GET`/`DELETE /mcp/oauth/grants`), una sezione UI in
  Settings, e la propagazione alla `McpOAuthTokenCache`. Superficie nuova = feature, non patch.
- **Trovato:** 2026-08-07

### `POST /register` non autenticata può saturare il tetto dei 200 client

- **Problema:** la Dynamic Client Registration è raggiungibile senza credenziali (per
  necessità: è il punto d'ingresso RFC 7591) e crea una riga `mcp_oauth_clients` per chiamata.
  Al raggiungimento di `_MAX_REGISTERED_CLIENTS = 200` ogni registrazione successiva riceve
  429 **per sempre**: non esiste pruning, scadenza o pulizia dei client mai usati.
- **Evidenza:** `backend/app/mcp/oauth.py:79` (`_MAX_REGISTERED_CLIENTS = 200`) e
  `register_client()` — il tetto è un contatore su tabella, senza rate limit né TTL.
- **Impatto:** basso e non catastrofico — serve `remote_mcp_enabled` ON ed endpoint esposto,
  e il percorso `/authorize` (che registra JIT ed è protetto dal token statico) continua a
  funzionare anche a tetto pieno, quindi i client già configurati non si rompono. Ma un
  operatore che aggiunge un *nuovo* connettore dopo la saturazione riceve un 429 opaco.
- **Sforzo:** S/M. La correzione giusta non è alzare il tetto ma dare un ciclo di vita ai
  client (TTL sui client senza token attivi + pruning), il che si incastra naturalmente con
  la voce precedente sulla superficie di revoca.
- **Trovato:** 2026-08-07

### pypdf: le advisory aperte sono salite da 24 a 40 — il salto a 6.x è sempre più urgente

- **Problema:** aggiornamento della voce del 2026-08-07 (che resta valida in pieno, incluso
  l'impatto e lo sforzo). `pip-audit` oggi riporta **40** advisory su `pypdf==5.9.0`, non più
  24: alle famiglie già note si sono aggiunte PYSEC-2026-1827…1833 (fix da 6.0.0 a 6.6.2) e
  PYSEC-2026-3655/3656 (fix in 6.15.0). Il quadro non cambia di natura — nessuna advisory è
  corretta in una 5.x, la fix più bassa è ora 6.0.0 e la più alta 6.15.0 — ma il ritmo con cui
  la lista cresce (+16 in una settimana) dice che rimandare ha un costo crescente.
- **Evidenza:** `backend/pyproject.toml:34` (`"pypdf>=4.2,<6"`); `.venv/bin/python -m pip_audit`
  al 2026-08-14.
- **Impatto:** invariato rispetto alla voce originale (pypdf è l'estrattore PDF di default e il
  fallback incondizionato di Marker/MinerU), ma la superficie cresce a ogni settimana di attesa.
- **Sforzo:** M — invariato. Resta un major, quindi fuori dal mandato della routine settimanale.
- **Trovato:** 2026-08-14

### Le dipendenze di build/test del frontend hanno 11 advisory, tutte dietro un major

- **Problema:** `npm audit` riporta 11 vulnerabilità (2 critical, 6 high, 3 moderate) su
  `vitest`/`@vitest/coverage-v8` (critical, `<=3.2.5`), `vite` (high, `<=6.4.2`), più le
  transitive `postcss`, `nanoid`, `esbuild`, `js-yaml`, `brace-expansion`, `fast-uri`,
  `@vitest/mocker`, `vite-node`. Le fix richiedono `npm audit fix --force`, cioè major su
  vitest e vite.
- **Evidenza:** `npm audit` da `frontend/` al 2026-08-14; `frontend/package.json`
  (`vite`, `vitest`, `@vitest/coverage-v8` in `devDependencies`).
- **Impatto:** **basso e circoscritto.** `npm audit --omit=dev` riporta **0 vulnerabilità**:
  nulla di tutto questo finisce nel bundle servito all'utente. È superficie di build e di test,
  sfruttabile solo da chi controlla già il sorgente o l'ambiente CI. Per questo NON è stata
  toccata in 2.1.9: un major su vite+vitest tocca la configurazione di build, i 123 file di test
  e la pipeline E2E — l'esatto opposto di una patch a basso rischio.
- **Sforzo:** M/L. Vanno fatti insieme (vitest 4 richiede vite 7), con rilettura di
  `vite.config.ts`, dei setup di vitest e del job E2E in CI.
- **Trovato:** 2026-08-14
