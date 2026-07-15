# Monitor CutApp — Cris Barbershop

Questo progetto controlla periodicamente le giornate prenotabili per il servizio configurato di Cris Barbershop. Quando CutApp pubblica nuove date, invia un messaggio Telegram.

Il programma **non prenota appuntamenti**, non usa ADB e non interagisce automaticamente con l'app: controlla soltanto l'elenco delle giornate disponibili.

## Requisiti

- Windows con Python 3.11 o successivo
- Un account CutApp valido
- Un bot Telegram e l'ID della chat destinataria

## Configurazione su Windows

Aprire PowerShell nella cartella del progetto ed eseguire:

```powershell
py -3.11 -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Aprire `.env` con un editor e compilare i valori mancanti. Il file deve mantenere questa struttura:

```dotenv
CUTAPP_USERNAME=
CUTAPP_PASSWORD=
CUTAPP_OWNER_CODE=valicenti
CUTAPP_SHOP_USERNAME=CrisBarbershop
CUTAPP_SERVICE=TAGLIO CAPELLI
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
CHECK_INTERVAL_MINUTES=10
```

Non condividere `.env` e non aggiungerlo a Git. Il progetto lo esclude già tramite `.gitignore`. In locale il programma legge questo file; in GitHub Actions legge invece le variabili d'ambiente configurate dal workflow.

## Uso

Con l'ambiente virtuale attivo, verificare prima Telegram:

```powershell
python monitor.py --test-telegram
```

Eseguire un singolo controllo:

```powershell
python monitor.py --once
```

Al primo controllo riuscito, il programma salva le date correnti in `data/state.json` senza segnalarle come nuove e invia un messaggio di inizializzazione.

Avviare il controllo continuo:

```powershell
python monitor.py --watch
```

Il primo controllo parte subito; i successivi rispettano `CHECK_INTERVAL_MINUTES`. Per fermare il monitor premere `Ctrl+C`.

## Stato locale e comportamento in caso di errore

- `data/state.json` contiene l'ultimo elenco ricevuto correttamente da CutApp ed è versionato per mantenere lo stato tra le esecuzioni di GitHub Actions.
- Il salvataggio usa un file temporaneo e una sostituzione atomica.
- Lo stato viene aggiornato anche quando CutApp rimuove delle date. Se una data rimossa viene riaperta, sarà quindi rilevata nuovamente.
- Se login, rete, token o risposta CutApp non sono validi, lo stato esistente non viene sovrascritto.
- Password e token vengono letti da `.env` in locale o dalle variabili d'ambiente in GitHub Actions e non vengono stampati.

## Esecuzione tramite GitHub Actions

Il workflow `CutApp Monitor` esegue automaticamente un controllo circa ogni 10 minuti e può essere avviato anche manualmente. Dopo un controllo riuscito, crea un commit soltanto se `data/state.json` è cambiato.

Nel repository GitHub aprire **Settings → Secrets and variables → Actions** e creare questi quattro repository secrets:

- `CUTAPP_USERNAME`
- `CUTAPP_PASSWORD`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

I valori non devono essere inseriti nel workflow, nel repository o nei log. Il file `.env` rimane esclusivamente locale e non viene caricato da GitHub Actions.

Per un avvio manuale, aprire la scheda **Actions** del repository, selezionare **CutApp Monitor**, scegliere **Run workflow** e confermare nuovamente con **Run workflow**. Se uno dei quattro secrets manca o è vuoto, il workflow termina con un errore che indica soltanto il nome del secret mancante.

## Avvio nelle sessioni successive

Quando si apre una nuova finestra PowerShell:

```powershell
Set-Location "F:\CutApp-Monitor"
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
python monitor.py --watch
```
