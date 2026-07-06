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

## Struttura del progetto

```
ios/
├── project.yml                 # spec XcodeGen (fonte di verità)
├── Synapse.xcodeproj           # progetto generato (committato per comodità)
└── Synapse/
    ├── App/                    # entry point, RootTabView, AppModel
    ├── Networking/             # SynapseClient, modelli Codable, AppSettings
    ├── Theme/                  # design tokens (colori, tipi pagina)
    ├── Shared/                 # componenti riutilizzabili (card, header, flow)
    └── Features/               # Wiki · Search · Chat · Graph · More/Settings
```

Per rigenerare il progetto dopo aver modificato `project.yml`:
`brew install xcodegen && cd ios && xcodegen generate`.
