# ADR-0055 — Settings IA v2: nav a due livelli, pagine focalizzate, config co-locata

- **Stato:** Accettata
- **Data:** 2026-07-04
- **Sostituisce:** ADR-0018 §5 (layout settings a left-nav piatta), layout A2.1 (5 gruppi-pagina)
- **Correlate:** ADR-0053 (runtime config overrides), ADR-0054 (domain vocabulary), R12-7/8/9 (ops schedules)

## Contesto

Il pannello impostazioni era un monolite (`SettingsPanel.tsx`, ~3.800 righe) con 5 voci di
navigazione ("Getting started", "AI & Models", "Sources", "Output", "Advanced"); ogni voce
renderizzava 3–6 sezioni eterogenee impilate in un'unica pagina a scorrimento. Problemi:

1. **Densità**: "Advanced" mescolava costi, sicurezza, manutenzione, override runtime,
   scheduler operazioni e about in una sola pagina.
2. **IA incoerente**: gli override runtime (ADR-0053) erano un elenco grezzo di chiavi in
   "Advanced", scollegato dal dominio funzionale (es. `embeddings_enabled` lontano dalla
   sezione Embeddings; `cost_alert_threshold_usd` lontano da Costi). Lo scheduling era
   diviso tra "Sources" (import) e "Advanced" (ops).
3. **Copertura**: i bound dei loop (I7: deep research, lint) erano configurabili solo via
   env, non dalla UI.
4. **Manutenibilità**: file singolo da 152 KB, test fragili, nessun deep-link alle sezioni.

## Decisione

1. **Nav a due livelli**: 6 intestazioni di gruppo non cliccabili + ~17 voci-pagina.
   Una pagina = una funzione. Attraversamento tastiera (frecce/Home/End) solo sulle voci.
2. **Struttura**:
   - **Generale** → Aspetto e lingua · Configurazione guidata
   - **AI e modelli** → Provider LLM · Scenari · Contesto e budget · Embeddings · Ricerca web
   - **Contenuti wiki** → Generazione · Automazioni (schedule S10–S13) · Limiti e budget AI (S14–S18)
   - **Sorgenti e import** → Cartelle sorvegliate · Web Clipper · PDF e conversione (S1–S3)
   - **Connessioni** → API e MCP (incl. CLI auth) · Sicurezza
   - **Sistema** → Costi (S4 co-locata) · Manutenzione · Informazioni
3. **Config co-locata**: ogni chiave runtime (ADR-0053) è renderizzata nella pagina del suo
   dominio funzionale, non in un elenco "Advanced". `SectionRuntimeConfig` resta l'unico
   renderer (prop `keys`).
4. **Allow-list estesa S14–S18** (`config_overrides.py`): `deep_research_max_iter`,
   `deep_research_token_budget`, `deep_research_max_queries`, `lint_max_iter`,
   `lint_token_budget` — interi con range validati; i read-site in `ops/deep_research.py` e
   `ops/lint.py` passano per `effective_int()`. I bound restano obbligatori (I7): la UI può
   regolarli entro range sicuri, mai disattivarli.
5. **Decomposizione file**: `settings/sections/*.tsx` (una sezione per file) + `ui.tsx`
   condiviso; `SettingsPanel.tsx` è solo shell (nav + routing pagina).
6. **Deep-link**: CustomEvent `synapse:settingsSection` (`detail.section` = pageId) per
   aprire una pagina specifica da altre superfici (stesso pattern di `synapse:openWizard`).

## Alternative considerate

- **Tenere 5 gruppi-pagina con ancore interne**: respinta — non risolve la densità né la
  scopribilità; lo scroll-spy aggiunge complessità senza chiarezza.
- **Esporre l'intero `Settings` env a runtime**: respinta — l'allow-list è un confine di
  sicurezza (ADR-0053 §2.4); chiavi infra/segreti (DATABASE_URL, token, endpoint interni)
  non devono essere raggiungibili dalla UI. L'estensione è curata, per chiavi operative.

## Conseguenze

- Ogni pagina è corta e monotematica; le impostazioni runtime sono dove l'utente le cerca.
- 5 nuove chiavi S14–S18 nel contratto GET/PUT/DELETE `/config/app` (ordine stabile esteso).
- I test FE sulla nav vanno aggiornati ai nuovi pageId; i `data-testid` delle sezioni sono
  invariati.
- D5 (screenshot Playwright) da rigenerare alla prossima gate di milestone.
