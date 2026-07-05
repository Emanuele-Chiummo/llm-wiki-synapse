# Proposta — Deep Research conversazionale + reranking (stile Perplexity/Gemini, in casa)

> Prodotta 2026-07-05 su richiesta owner. **Proposta**, non roadmap approvata.
> Colloca il lavoro **dopo** il multi-vault (v1.4 → 2.0.0 resta la priorità):
> candidata a una v2.1 «AI Research Hub». Nessuna nuova infrastruttura — riusa
> SearXNG, bge-m3, Qdrant e il provider layer già in esecuzione (I9).

---

## 0. Tesi

Il loop di deep research di Synapse **è già** il pattern di Perplexity/Gemini:
pianifica query → cerca (SearXNG) → leggi → valuta i gap → ri-cerca → sintetizza
con citazioni → auto-ingest. Ciò che separa Synapse dai prodotti commerciali non
è l'architettura ma **tre leve di qualità/UX**, tutte realizzabili con i mattoni
già presenti. E il posizionamento vincente non è «un Perplexity in casa» ma
**«il Perplexity che tiene ciò che impara»**: ogni ricerca diventa conoscenza
permanente nel grafo (K1–K8), self-hosted, a costo marginale zero, senza dati in
uscita.

### Cosa esiste già (v1.3.3)

`backend/app/ops/deep_research.py` — loop bounded (I7: `max_iter` +
`token_budget`, costo loggato):
1. `_generate_queries` — l'LLM deriva N query dal topic
2. `_search_searxng` — SearXNG, concorrenza 3 (I9, Do-NOT #3: unico path web)
3. `_fetch_and_extract` — fetch + HTML→markdown; PDF via estrattore ingest
   (Marker/pypdf, dalla 1.3.3); SSRF-guarded (R13-9)
4. `_assess_sufficiency` — l'LLM giudica SUFFICIENT|INSUFFICIENT + gap
5. refine + loop finché sufficiente o cap
6. `_synthesize` → pagina con citazioni `[n]` → auto-ingest

### Cosa NON esiste (i divari, in ordine di ROI)

| # | Divario | Oggi | Perplexity/Gemini |
|---|---------|------|-------------------|
| G1 | **Reranking** dei contenuti recuperati | fetch → tutto nel prompt | riordino per rilevanza prima della sintesi |
| G2 | **Modalità conversazionale** | job batch → pagina wiki letta dopo | risposta in chat, streaming, follow-up |
| G3 | **Estrazione + pianificazione** | `_html_to_markdown` ingenuo, query piatte | Readability + decomposizione multi-hop |

---

## R1 — Reranking con bge-m3 (leva di qualità n°1)

**Problema.** `_fetch_and_extract` tronca ogni fonte a `FETCH_MAX_CHARS` e passa
tutto al modello di sintesi. Rumore alto (menu, boilerplate, sezioni off-topic),
token sprecati, sintesi diluita.

**Soluzione.** Riusare la pipeline di embedding già in `app/embeddings.py`
(bge-m3) e `app/qdrant_client.py`:
- chunk dei contenuti recuperati (finestra ~512 token, overlap)
- embed dei chunk + embed della query
- cosine top-k → al modello di sintesi vanno **solo i chunk migliori**

**Perché è pulito.** Nessuna nuova dipendenza: bge-m3 e Qdrant sono già il cuore
del retrieval (`rag/retrieval.py`). Il reranking dei chunk di deep-research può
usare un vettore effimero in memoria (non serve persistere in Qdrant — sono
transienti). Rispetta I7 (meno token = meno costo) e I9 (riuso infra).

**Effort.** M. **Impatto.** Alto — è il singolo passo che alza di più la qualità.

---

## R2 — Deep Research → Chat conversazionale (leva di UX n°1)

**Problema.** Oggi la ricerca è un lavoro in background che scrive una pagina
wiki: ottimo per la memoria, ma non è l'esperienza «fai una domanda, ricevi una
risposta con fonti» di Perplexity. Manca il **ramo conversazionale**.

**Soluzione.** Un secondo punto d'uscita del loop, **senza duplicarlo**:
- in chat, un toggle «Ricerca approfondita» sul messaggio
- il loop esistente gira, ma la sintesi finale **strema nella chat** (riusa lo
  streaming F6/F7 + citazioni `[n]` già cliccabili dalla 1.3.3) invece di (o in
  aggiunta a) scrivere la pagina
- le fonti appaiono progressivamente (già le abbiamo per-source)
- **follow-up**: la conversazione mantiene il contesto della ricerca; una
  domanda successiva riusa le fonti raccolte prima di ri-cercare

**Il differenziatore Synapse.** A fine risposta, un pulsante **«Salva nella
wiki»** (già esiste per la chat normale): la ricerca conversazionale diventa
opzionalmente conoscenza permanente. Perplexity dimentica; qui **l'umano
decide** cosa tenere (K8). Questo è il ponte tra i due mondi.

**Invarianti.** I3 (parse a fine stream, niente lavoro per-token), I7 (stesso
loop bounded), I6 (nessun provider hardcoded — la ricerca eredita il provider
della chat). Nessun nuovo loop AI (roadmap §OOS rispettata: si *ricompone* il
loop esistente, non se ne aggiunge uno).

**Effort.** L. **Impatto.** Alto — è la differenza di *esperienza* più visibile.

---

## R3 — Estrazione e pianificazione più fini (ciliegina)

Tre micro-migliorie indipendenti, ognuna piccola:

- **R3a — Estrazione Readability-style.** `_html_to_markdown` prende tutto il
  DOM. Un estrattore del contenuto principale (readability-lxml o trafilatura,
  puro Python, opt-in come Marker) elimina menu/pubblicità/footer prima del
  reranking. Effort S.
- **R3b — Query multi-hop.** `_generate_queries` produce N query piatte.
  Decomposizione in sotto-domande (l'LLM pianifica «per rispondere a X servono
  X1, X2, X3») migliora la copertura su topic composti. Effort S/M, dentro il
  loop esistente.
- **R3c — Filtro temporale SearXNG.** `ops/searxng.py` non usa `time_range`.
  Esporlo (giorno/settimana/mese/anno) rende utili le query «cosa è successo di
  recente su X». Effort S — è un parametro già supportato dal motore.

---

## Sequenziamento e priorità

1. **Prima il multi-vault** (v1.4 → 2.0.0). Non ci si ripensa: è il breaking
   change che regge tutto il resto.
2. **v2.1 «AI Research Hub»** — nell'ordine: **R1 (reranking)** → **R2
   (conversazionale)** → **R3 (rifiniture)**. R1 alza la qualità a costo M; R2
   la trasforma in esperienza; R3 è incrementale e opzionale.

Se si vuole un assaggio prima del multi-vault, **R1 e R3c** sono isolati,
retrocompatibili e migliorano subito la ricerca esistente senza toccare la UI.

## Fuori scope (coerente con la roadmap)

- Crawler proprietario / indice web (SearXNG È il backend, I9).
- Retrieval cross-vault di default; auto-merge senza umano; daemon di
  manutenzione autonomo. Il pattern dice: l'umano cura, l'AI mantiene (K8).
- Inseguire la qualità del singolo-risultato dei modelli frontier: il fossato di
  Synapse è la **memoria persistente + privacy + costo zero**, non la potenza
  bruta del modello.

## ADR da produrre (se approvata)

- **ADR-00xx — Reranking bge-m3 dei contenuti deep-research** (R1): dove si
  inserisce nel loop, chunking, vettori effimeri vs Qdrant, budget token.
- **ADR-00xx — Ramo conversazionale del deep research** (R2): secondo exit del
  loop, contratto di streaming in chat, semantica del follow-up, save-to-wiki.
