# ADR-0057 — Strategia responsive per iPhone e iPad (mobile/tablet/desktop a 3 tier)

- **Stato:** Accettata
- **Data:** 2026-07-04
- **Invarianti:** I2 (layout server-side, niente force-layout sul main thread), I3 (niente lavoro pesante per-token), I4 (liste virtualizzate)
- **Correlate:** ADR-0055 (Settings IA v2), F1 (shell 3 pannelli), F15 (cross-platform/PWA), UXA-27 (toolbar grafo)

## Contesto

La shell a 3 pannelli (tree / centro / preview, `PanelGroup.tsx` +
`react-resizable-panels`) è nata desktop-first. Il supporto mobile esistente è un
singolo strato CSS in `theme.css` con **due breakpoint incoerenti** (767px e 720px
legacy) che si limita a *nascondere* i pannelli laterali: su iPhone l'albero wiki e
il pannello preview sono di fatto irraggiungibili (il bottone di collapse desktop non
è pensato per touch), su iPad in verticale (768–834px di larghezza) si ricade nel
layout desktop a 3 pannelli con colonne troppo strette per essere usabili. Mancano
inoltre: gestione delle safe-area iOS (notch / home indicator), altezza corretta con
la barra URL dinamica di Safari (`100vh` → contenuto tagliato), e uno stato UI
condiviso per apertura/chiusura dei pannelli (oggi `useState` locale in PanelGroup).

## Decisione

### 1. Tre tier di viewport, un solo set di breakpoint

| Tier | Media query | Dispositivi target | Layout shell |
|------|-------------|--------------------|--------------|
| **mobile** | `max-width: 767px` | iPhone (390–430px portrait, ≤740px landscape) | 1 pannello: centro full-width; tree e preview come **drawer** |
| **tablet** | `768px – 1023px` | iPad portrait (768/810/834px) | 2 pannelli: tree + centro; preview come drawer da destra; nav rail solo icone |
| **desktop** | `min-width: 1024px` | iPad landscape (1024/1180px), desktop | 3 pannelli attuali, invariati |

Il breakpoint legacy 720px viene **unificato a 767px**. I valori sono definiti una
sola volta: in CSS come commento-contratto in testa a `theme.css`, in TS come
costanti in `src/utils/viewport.ts` (unica fonte per `matchMedia`).

### 2. Hook `useViewport()` (React, no resize-listener sparsi)

`src/hooks/useViewport.ts`: `useSyncExternalStore` su due `matchMedia` →
`"mobile" | "tablet" | "desktop"`. Nessun listener `resize` manuale, nessun
re-render per-pixel (I3): il valore cambia solo all'attraversamento del breakpoint.
Tutte le decisioni di layout in JSX passano da questo hook; il CSS continua a
gestire ciò che è puramente presentazionale.

### 3. Drawer per tree e preview (mobile) e preview (tablet)

Nuovo componente `PanelDrawer` (overlay con backdrop, slide-in 80% width max
360px, `role="dialog"` + focus trap + chiusura con Esc/backdrop/selezione):

- **mobile:** tree drawer da sinistra (aperto da bottone nel header di sezione),
  preview drawer da destra (aperto da azione "dettagli" sul contenuto attivo).
  La selezione di una pagina nel tree chiude il drawer e mostra il contenuto.
- **tablet:** solo preview come drawer; il tree resta pannello fisso.

Lo stato open/closed vive in un nuovo store Zustand `uiStore.ts` (con selectors,
I3), che assorbe anche il collapse desktop oggi in `useState` locale — un'unica
fonte per "quali pannelli sono visibili". Le liste dentro i drawer restano
virtualizzate (TanStack Virtual — I4): il drawer monta lo **stesso** componente
NavTree/Preview, cambia solo il contenitore.

### 4. iOS: safe-area e viewport dinamico

- `index.html`: `viewport-fit=cover` nel meta viewport.
- App shell: altezza `100dvh` (fallback `100vh`), padding con
  `env(safe-area-inset-left/right/bottom)` su nav rail, drawer e barre inferiori.
- Il canvas sigma mantiene `touch-action: none` (già presente); la toolbar zoom
  del grafo deve restare visibile e raggiungibile su touch in tutti i tier
  (chiude anche UXA-27 per la parte mobile).

### 5. Cosa NON cambia

- Nessun re-layout del grafo lato client (I2): cambiare tier cambia solo CSS/JSX.
- CodeMirror 6 resta l'editor su tutti i tier (I4); niente editor "mobile" separato.
- Le stringhe nuove (aria-label, bottoni drawer) passano da i18n EN/IT (test di
  parità chiavi).

## Conseguenze

- iPhone: flusso completo utilizzabile (naviga tree → leggi/edita → chat → grafo)
  senza pannelli irraggiungibili; niente contenuto sotto notch/home indicator.
- iPad portrait smette di essere un desktop compresso; iPad landscape resta
  identico al desktop (zero regressioni per l'uso primario).
- Un solo posto per i breakpoint e per lo stato dei pannelli: le viste future
  (vault switcher R15-1, Home R15-2) nascono già responsive usando `useViewport()`
  + `uiStore`.
- Costo: nuovo `uiStore` e migrazione del collapse di PanelGroup; test vitest per
  hook/drawer/store; verifica Playwright con viewport iPhone (390×844) e iPad
  (834×1194, 1194×834) da integrare nella suite E2E quando il job CI (R13-8a)
  sarà attivo.
