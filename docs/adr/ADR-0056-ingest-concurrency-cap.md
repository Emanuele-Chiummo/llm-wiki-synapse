# ADR-0056 — Bounded watcher ingest concurrency (INGEST_MAX_CONCURRENCY)

- **Stato:** Accettata
- **Data:** 2026-07-04
- **Invariante:** I7 (loops/fan-out bounded)
- **Correlate:** ADR-0001 (mtime-then-hash ingest), ADR-0003 (ingest seam), ADR-0006 (no startup rescan), ADR-0046 (ingest queue)

## Contesto

Il watcher (`app/watcher.py`) crea **un task di ingest per ogni file** modificato
(`_fire` → `create_task(_run)`). L'unico controllo esistente, `_inflight`, è un dedup
**per singolo path**: impedisce due ingest concorrenti *dello stesso* file, ma non pone
alcun limite al numero di file **diversi** processati insieme.

In produzione (TrueNAS SCALE, RTX 3060 12 GB, host senza swap) un utente ha copiato in
blocco decine di `.md` in `raw/sources/`. Il watcher ha lanciato decine di ingest
simultanei; con provider CLI (`claude-agent-sdk`) ogni ingest avvia un intero agente più
una richiesta di embedding. Effetti a cascata osservati nei log:

- `sqlalchemy.exc.TimeoutError: QueuePool limit of size 5 overflow 10 reached` — pool DB
  esaurito (`db.py`: `pool_size=5, max_overflow=10`);
- Ollama `500 Internal Server Error` sull'endpoint embeddings — GPU singola sommersa da
  richieste concorrenti;
- picco di RAM senza swap → OOM → **crash dell'host**.

La causa radice è la concorrenza dell'ingest **illimitata**, in violazione dello spirito
di I7 ("ogni loop/fan-out ha un tetto").

## Decisione

Introdurre un **semaforo asyncio** in `_MarkdownHandler` che limita gli ingest concorrenti
a `settings.ingest_max_concurrency` (env `INGEST_MAX_CONCURRENCY`, **default 3**, coerciato
a ≥ 1). Il task per file viene comunque creato subito, ma il lavoro pesante in `_run`
attende il semaforo: al massimo N ingest girano insieme, il resto si accoda a costo
trascurabile (`acquire()`) e scala man mano che un posto si libera. Ogni file viene
comunque processato — cambia solo il *quando*, non il *se*.

Il default 3 è tenuto ben sotto la dimensione del pool DB (15) così che gli ingest
concorrenti, più le query servite dagli endpoint, non saturino il pool. Il semaforo gate
anche le delete (stesso data-plane).

## Alternative considerate

- **Allargare il pool DB / aggiungere swap**: mitigano i sintomi ma non il fan-out
  illimitato; un burst abbastanza grande satura comunque GPU e memoria. Restano consigli
  operativi validi (lo swap in particolare), non la fix.
- **Coda con worker pool dedicati**: più codice; il semaforo ottiene lo stesso bound con
  una riga, riusando lo scheduling asyncio esistente.
- **Throttle a monte nell'ImportScheduler**: coprirebbe solo il path `/import`, non il
  drop diretto in `raw/sources/` via SMB/editor.

## Conseguenze

- Un drop in blocco di N file non può più floodare DB, embedding host o RAM: il carico è
  limitato per costruzione (I7 soddisfatta anche sul fan-out dell'ingest).
- Nuova env `INGEST_MAX_CONCURRENCY` (default 3) — regolabile per host più capienti.
- Bulk load grandi diventano semplicemente più lenti e stabili invece di fatali.
- Nessun cambiamento di schema/API; nessun impatto su ER/OpenAPI.
