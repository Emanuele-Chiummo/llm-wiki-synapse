# Runbook — manutenzione settimanale autonoma

> **Chi lo esegue:** una Claude Code Routine cloud, ogni **venerdì alle 16:00 UTC**
> (18:00 Europe/Rome durante l'ora legale, 17:00 durante l'ora solare).
> **Cosa produce:** una patch release `2.1.x`, oppure — legittimamente — nessuna release.
> **Autonomia:** piena, fino alla pubblicazione della release inclusa.
>
> Questo file è la fonte di verità del processo. La routine cloud contiene solo un prompt
> breve che punta qui: **per cambiare il comportamento settimanale si modifica questo
> documento**, non la routine.

---

## 0. Contesto minimo

Synapse è un wiki AI self-hosted: FastAPI (Python 3.11+) + React 19/Vite + Tauri v2 desktop,
con Postgres e Qdrant. Prima di toccare qualsiasi cosa:

1. Leggi **`CLAUDE.md`** — contiene gli invarianti **I1–I9 non negoziabili**, le convenzioni
   di commit e lo stack. Nessun fix può violarli.
2. Leggi le prime ~100 righe di **`CHANGELOG.md`** — cosa è cambiato nelle ultime release.
3. `git log --oneline -30` — contesto recente.

---

## 1. Regola d'oro: non inventare lavoro

Se dopo un'analisi seria non emerge nulla che valga davvero un fix, **non forzare una
release**. Si scrive il report dicendo che il codice è in salute, elencando cosa è stato
controllato e con quale profondità.

Una settimana senza release è un esito positivo. Un fix inventato per "riempire" la
settimana introduce rischio in un sistema che funziona: è un danno netto.

---

## 2. Cosa cercare, in ordine di priorità

1. **Bug veri** — logica errata, race condition, gestione errori mancante, edge case
   scoperti, risorse non rilasciate, `except` troppo larghi che mascherano errori reali.
2. **Stabilità** — task asincroni senza strong reference (garbage-collected a metà),
   loop senza bound (viola I7), timeout mancanti, retry senza backoff, query N+1,
   sessioni/transazioni DB lasciate aperte.
3. **Sicurezza** — segreti che finiscono nei log, input non validati, path traversal,
   auth aggirabile, dipendenze con CVE note (`pip-audit`, `npm audit`).
4. **Copertura test mancante** su codice critico toccato di recente.
5. **Drift documentale** — documentazione che contraddice il codice: versioni stale,
   endpoint rinominati, ADR con `Status` sbagliato rispetto a ciò che è implementato.
6. **Dipendenze** — aggiornamenti **patch/minor** sicuri. Mai major.

**Dove guardare per primo.** Il debito fresco si annida nel codice cambiato di recente:

```bash
git log --since="3 weeks ago" --name-only --pretty=format: | sort -u | grep -v '^$'
```

Poi si allarga ai moduli critici (i "centri di gravità" del progetto):
`backend/app/ingest/`, `backend/app/runtime_state.py`, `backend/app/rag/retrieval.py`,
`backend/app/mcp/`, `backend/app/ops/`, `frontend/src/store/`, `frontend/src/components/`.

---

## 3. Vincoli non negoziabili

- **È una PATCH release.** Nessuna feature nuova, nessun breaking change, nessuna
  migrazione di schema DB salvo necessità stretta — e se una migrazione serve davvero,
  è il segnale che il fix è troppo grosso per questa routine: va nel backlog 3.0 (§6).
- **Mantra di non-regressione.** I flussi esistenti restano funzionalmente intatti. Ogni
  fix va accompagnato da un **test di regressione che fallirebbe senza il fix**.
- **Mai** `git push --force`, mai riscrivere la history, mai toccare tag già pubblicati.
- **Mai commit diretti su `main`** (la branch protection lo blocca comunque): sempre
  branch + PR.
- **Ambito ragionevole:** puntare a **1–5 fix mirati**. Meglio poco e solido.
- **Mai retry alla cieca** su una CI rossa: si leggono i log, si capisce la causa, si
  corregge. Un rerun si giustifica solo dopo aver *verificato* che il fallimento è
  transitorio (es. timeout di rete in upload).

---

## 4. Gate di qualità — tutti verdi prima della PR

**Backend** (da `backend/`; se manca il venv: `python -m venv .venv && .venv/bin/pip install -e ".[dev]"`):

```bash
ruff check app/ tests/
black --check app/ tests/
mypy app          # strict
pytest -q         # ~3160 test — deve restare verde, coverage gate 82%
```

**Frontend** (da `frontend/`; `npm ci` se serve):

```bash
npx tsc --noEmit
npx eslint src/
npx prettier --check "src/**/*.{ts,tsx,css}" vite.config.ts
npx vitest run    # ~2550 test
```

Se un gate fallisce **per colpa delle modifiche fatte**, si sistema. Se fallisce per un
problema preesistente e scorrelato, **non si forza**: si annota nel report e si restringe
l'ambito della settimana.

---

## 5. Rilascio (solo se è stato fatto almeno un fix)

Prossima patch: `2.1.7` → `2.1.8` → `2.1.9` → … Nell'ordine esatto:

1. **Branch:** `git checkout -b fix/weekly-<YYYY-MM-DD>`
2. **Commit dei fix.** Formato: `fix(<modulo>): <descrizione> [<feature-id>]` — ogni commit
   referenzia almeno un ID feature (`K1`–`K8` / `F1`–`F18`, vedi `CLAUDE.md` §4).
   Firma: `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`
3. **Bump versione:** `python scripts/bump_version.py bump X.Y.Z`
   → deve stampare `All files agree` (7 superfici allineate).
4. **Rigenera gli artefatti** — *se salti questo passo il Docs Gate in CI fallisce.*
   È già successo: una release è stata bloccata per un `schema.mmd` non rigenerato.

   ```bash
   cd backend && python scripts/generate_openapi.py && python scripts/generate_er.py && cd ..
   python3 scripts/check_adr_index.py    # deve dire "0 error(s)"
   ```

   L'ER va rigenerato **ogni volta che cambiano i modelli SQLAlchemy** (invariante I8).
5. **`CHANGELOG.md`** — nuova sezione in cima, Keep-a-Changelog + SemVer:
   `## [X.Y.Z] — YYYY-MM-DD — "nome-breve"`, con `### Fixed` / `### Changed` / `### Security`.
   Si spiega **la causa radice**, non il sintomo.
6. **`docs/release-notes/vX.Y.Z.md`** — versione estesa dello stesso contenuto, più una
   sezione `## Upgrade notes`.
7. **`mkdocs.yml`** — voce in cima a `nav:` → `Release Notes:`.
8. **Commit release:** `chore(release): X.Y.Z — <nome-breve>`
9. **Push + PR:** `gh pr create` con Summary + Test plan (checkbox spuntate su ciò che è
   stato realmente eseguito).
10. **Attendere la CI:** `gh pr checks <N>` in polling. I job durano fino a ~7 minuti; gli
    E2E (Playwright + Docker) sono i più lenti. **Non mergiare finché non è tutto verde.**
    Su fallimento: `gh api repos/Emanuele-Chiummo/llm-wiki-synapse/actions/jobs/<job-id>/logs`.
11. **Merge:** `gh pr merge <N> --squash --delete-branch`
12. **Rilascio:**

    ```bash
    gh workflow run release-cut.yml -f version=X.Y.Z
    gh workflow run release-notes-sync.yml
    gh workflow run desktop-release.yml --ref vX.Y.Z
    ```

    Attendere il completamento della desktop-release. Tempi normali: macOS ~8-10 min,
    Windows ~10 min, **`marker-image` ~25-35 min** (build multi-arch con PyTorch: è
    lenta, *non* bloccata — non interromperla). Verifica finale:

    ```bash
    gh release view vX.Y.Z --json assets -q '.assets[].name'
    ```

    Devono comparire sia gli asset macOS (`.dmg`, `.app.tar.gz` + `.sig`) sia Windows
    (`_x64-setup.exe` + `.sig`, `portable-x64.exe`).

Se `gh` non risulta autenticato nell'ambiente: **fermarsi dopo il push del branch** e
dirlo esplicitamente nel report.

### Trabocchetti già pagati (non ripeterli)

| Sintomo | Causa | Prevenzione |
|---|---|---|
| Docs Gate rosso | ER/OpenAPI non rigenerati | Passo 4, sempre |
| Docs Gate rosso | ADR nuovo non linkato in `docs/adr/index.md` | `check_adr_index.py` in locale |
| Windows build rossa | `windows-latest` usa PowerShell | `shell: bash` esplicito sugli step POSIX |
| `gh run rerun` rifiutato | Il run è ancora in corso | Aspettare che *tutti* i job finiscano |
| Upload asset fallito | Timeout TLS transitorio | Rerun del solo job fallito, a run concluso |

---

## 6. Backlog 3.0

Se emerge qualcosa di **troppo grosso per una patch** — refactor architetturali, feature,
breaking change, migrazioni pesanti — **non si fa**. Si aggiunge una voce a
[`docs/reference/ROADMAP-3.0-IDEAS.md`](../reference/ROADMAP-3.0-IDEAS.md) con:

- titolo
- problema osservato
- evidenza (`file:riga`)
- impatto stimato
- sforzo stimato (S / M / L)

Il file aggiornato entra nella PR della settimana insieme ai fix.

---

## 7. Report finale

Si chiude **sempre** con un report in italiano, anche a zero release:

- **Cosa è stato analizzato** — aree, file, profondità.
- **Bug trovati e corretti** — ognuno con la causa radice in una frase.
- **Cosa è stato deliberatamente NON toccato** e perché.
- **Stato release** — link alla release GitHub + conferma asset macOS/Windows, oppure il
  motivo per cui non c'è stata release.
- **Nuove voci** aggiunte al backlog 3.0.
- **Decisioni che richiedono un umano** — tutto ciò che è stato lasciato in sospeso.
