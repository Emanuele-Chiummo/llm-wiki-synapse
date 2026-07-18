# Synapse — App iOS nativa

Client iOS nativo (SwiftUI) per il backend Synapse. Sfoglia il wiki, cerca,
chatta con citazioni, esplora il knowledge graph e gestisci la coda di revisione
— tutto contro il tuo server Synapse self-hosted.

- **Linguaggio:** Swift 5 / SwiftUI, target iOS 17+
- **Progetto:** generato con [XcodeGen](https://github.com/yonaskolb/XcodeGen) da `project.yml`
- **Bundle id:** `ai.synapse.mobile`

Al primo avvio apri **Altro → Impostazioni → URL del server** e inserisci
l'indirizzo del tuo backend (es. `https://synapse.tuodominio.com` oppure
`http://192.168.1.10:8000`). Il token è opzionale — serve solo se sul server hai
impostato `SYNAPSE_AUTH_TOKEN`.

---

## Compilare ed eseguire (Xcode)

Requisiti: macOS con **Xcode 16+** (testato con Xcode 26), un **Apple ID**
gratuito.

```bash
cd ios
xcodegen generate     # rigenera Synapse.xcodeproj da project.yml (se serve)
open Synapse.xcodeproj
```

> Il progetto `Synapse.xcodeproj` è già incluso nel repo: puoi anche aprirlo
> direttamente senza lanciare `xcodegen`.

### Sul Simulatore (nessuna firma)
Seleziona un **iPhone Simulator** come destinazione e premi **Run (⌘R)**. Il
simulatore non richiede firma né account sviluppatore.

---

## Auto-firmare e installare sul tuo iPhone (Apple ID gratuito)

Non serve un account Apple Developer a pagamento: basta un Apple ID normale
("Personal Team"). L'app resta valida **7 giorni**, poi va reinstallata.

1. **Collega l'iPhone** al Mac col cavo, sbloccalo e tocca **"Autorizza"**.
2. In Xcode seleziona il target **Synapse** → tab **Signing & Capabilities**:
   - spunta **Automatically manage signing**
   - **Team** → scegli il tuo Apple ID (*Personal Team*)
   - se il **Bundle Identifier** `ai.synapse.mobile` risultasse già in uso,
     cambialo in uno personale, es. `com.tuonome.synapse`
3. **Abilita la Modalità sviluppatore sull'iPhone** (iOS 16+):
   Impostazioni → Privacy e sicurezza → **Modalità sviluppatore** → attiva →
   riavvia.
4. Seleziona **il tuo iPhone** come destinazione in alto e premi **Run (⌘R)**.
5. Alla prima esecuzione, sul telefono: Impostazioni → Generale →
   **VPN e gestione dispositivi** → **Fidati** del tuo certificato sviluppatore.

### ⚠️ Gotcha: repo dentro iCloud Drive
Se il repo è sotto `~/Documents` o `~/Desktop` sincronizzati con iCloud, la
firma può fallire con **"resource fork, Finder information, or similar detritus
not allowed"** (iCloud aggiunge attributi estesi ai file compilati). Rimedi:
- Xcode → **Settings → Locations → Derived Data → Default**
  (così la build sta in `~/Library`, fuori da iCloud), **oppure**
- sposta il repo fuori da iCloud (es. `~/Developer/`).

---

## Sideload dell'`.ipa` (senza Xcode)

Nelle release GitHub è allegato un **`Synapse-<versione>-unsigned.ipa`** non
firmato. Puoi installarlo/ri-firmarlo col tuo Apple ID usando:

- **[AltStore](https://altstore.io)** — installa AltServer sul computer, poi da
  iPhone: AltStore → **+** → scegli l'`.ipa`. Firma con il tuo Apple ID e si
  auto-rinnova ogni 7 giorni finché AltServer è raggiungibile.
- **[Sideloadly](https://sideloadly.io)** — collega l'iPhone, trascina l'`.ipa`,
  inserisci l'Apple ID e premi **Start**.

Entrambi usano lo stesso meccanismo "Personal Team": app valida 7 giorni,
rinnovabile. Per una durata di 1 anno serve un account **Apple Developer** a
pagamento (99 €/anno).

---

## TestFlight (distribuzione beta) — ⚠️ richiede azione del proprietario

La pipeline TestFlight è **predisposta ma non eseguibile in questo ambiente**: manca
un account **Apple Developer Program a pagamento** e una **App Store Connect API
key**. Nessuna credenziale è stata inventata (mantra "chiedere, non aggirare"). Lo
script `ios/scripts/testflight.sh` si **rifiuta di partire** finché i segreti non
sono presenti, invece di fingere un caricamento.

### Cosa serve dal proprietario (una tantum)
1. **Apple Developer Program a pagamento** (99 €/anno). Il team attuale in
   `project.yml` (`DEVELOPMENT_TEAM: 4SUH9X5QWS`) è un *Personal Team* gratuito, che
   **non può** caricare su TestFlight. Sostituirlo con il team id a pagamento.
2. **App identifier registrato**: `ai.synapse.mobile` in
   [Certificates, Identifiers & Profiles](https://developer.apple.com/account/resources).
3. **App Store Connect API key** ([Users and Access → Integrations → App Store
   Connect API](https://appstoreconnect.apple.com/access/integrations/api)):
   scaricare il file `AuthKey_<KEYID>.p8` e annotare **Key ID** e **Issuer ID**.
4. Un record **App** creato in App Store Connect con quel bundle id.

### Come pubblicare una build (una volta ottenute le credenziali)
```bash
# aggiornare teamID in ios/ExportOptions-AppStore.plist (o passarlo via TEAM_ID)
ASC_KEY_ID=XXXXXXXXXX \
ASC_ISSUER_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx \
ASC_KEY_P8=~/private_keys/AuthKey_XXXXXXXXXX.p8 \
TEAM_ID=YOURPAIDTEAM \
ios/scripts/testflight.sh
```
Lo script rigenera il progetto, archivia in Release, esporta l'`.ipa` con
`ExportOptions-AppStore.plist` (metodo `app-store-connect`, firma automatica) e lo
carica con `xcrun altool`. La build appare in **App Store Connect → TestFlight**
dopo l'elaborazione; da lì si assegnano i tester.

> Le stesse variabili possono alimentare un job CI (GitHub Actions) in futuro,
> conservando la `.p8` come secret del repo. Non incluso qui: senza le credenziali
> reali il workflow non è verificabile.

---

## Struttura del progetto

Il redesign (Track 2.1) è l'unica esperienza: shell nativa a 5 tab
(Home · Wiki · Chat · Graph · More) su `SynapseSession` + client 2.0.0. Il vecchio
tema (`Theme.swift`, accento Apple-indigo + nero pieno) e le `Features/` legacy sono
stati **ritirati in Fase C** — resta un solo linguaggio visivo (`SynColor`).

```
ios/
├── project.yml                 # spec XcodeGen (fonte di verità)
├── ExportOptions-AppStore.plist# export TestFlight (teamID da compilare)
├── scripts/testflight.sh       # pipeline TestFlight (richiede credenziali owner)
├── Synapse.xcodeproj           # progetto generato (committato per comodità)
└── Synapse/
    ├── App/SynapseApp.swift    # entry point (root = redesign shell)
    ├── DesignSystem/           # SynColor/SynMetrics + SynButton/Card/Chip/…
    ├── Redesign/
    │   ├── Data/               # APIClient (2.0.0), SynapseSession, SSE, DTO, Keychain
    │   ├── Graph/              # Graph tab: renderer swappable + Canvas nativo
    │   ├── Review/             # coda di revisione (F9)
    │   ├── Sources/            # browser sorgenti + attività ingest
    │   ├── Settings/           # provider (F17), vault, lingua
    │   └── *.swift             # Home · Wiki · Chat · Search · More · Tokens
    └── Shared/FlowLayout.swift # layout a scorrimento riusato dalla chat
```

Per rigenerare il progetto dopo aver modificato `project.yml`:
`brew install xcodegen && cd ios && xcodegen generate`.
