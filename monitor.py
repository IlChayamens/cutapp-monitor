"""Monitor delle nuove giornate prenotabili su CutApp per Cris Barbershop."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import requests
from dotenv import dotenv_values


BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"
STATE_DIR = BASE_DIR / "data"
STATE_FILE = STATE_DIR / "state.json"

LOGIN_URL = "https://cutapp.azurewebsites.net:443/home/login2"
CALENDAR_URL = (
    "https://cutapp.azurewebsites.net:443/clienti/getGiorniLavoro/withInfoN"
)
HTTP_TIMEOUT_SECONDS = 30
CONFIG_VARIABLE_NAMES = (
    "CUTAPP_USERNAME",
    "CUTAPP_PASSWORD",
    "CUTAPP_OWNER_CODE",
    "CUTAPP_SHOP_USERNAME",
    "CUTAPP_SERVICE",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "CHECK_INTERVAL_MINUTES",
)

WEEKDAYS_IT = (
    "lunedì",
    "martedì",
    "mercoledì",
    "giovedì",
    "venerdì",
    "sabato",
    "domenica",
)
MONTHS_IT = (
    "",
    "gennaio",
    "febbraio",
    "marzo",
    "aprile",
    "maggio",
    "giugno",
    "luglio",
    "agosto",
    "settembre",
    "ottobre",
    "novembre",
    "dicembre",
)


class MonitorError(Exception):
    """Errore previsto e sicuro da mostrare all'utente."""


class ConfigurationError(MonitorError):
    """Configurazione mancante o non valida."""


class CutAppError(MonitorError):
    """Errore durante una richiesta a CutApp."""


class TelegramError(MonitorError):
    """Errore durante una richiesta a Telegram."""


class StateError(MonitorError):
    """Errore durante la lettura o scrittura dello stato locale."""


@dataclass(frozen=True)
class TelegramConfig:
    bot_token: str
    chat_id: str


@dataclass(frozen=True)
class MonitorConfig:
    username: str
    password: str
    owner_code: str
    shop_username: str
    service: str
    telegram: TelegramConfig
    check_interval_minutes: int


def read_configuration() -> Mapping[str, str | None]:
    """Legge le variabili dall'ambiente oppure, in locale, dal file .env."""
    file_values: Mapping[str, str | None] = {}
    if ENV_FILE.is_file():
        try:
            # interpolate=False evita riferimenti impliciti ad altre variabili.
            file_values = dotenv_values(ENV_FILE, interpolate=False)
        except (OSError, ValueError) as exc:
            raise ConfigurationError("Impossibile leggere il file .env.") from exc

    # Le variabili del processo hanno precedenza. GitHub Actions usa questo
    # percorso e non deve creare né caricare alcun file .env.
    return {
        name: os.environ[name] if name in os.environ else file_values.get(name)
        for name in CONFIG_VARIABLE_NAMES
    }


def required_value(values: Mapping[str, str | None], name: str) -> str:
    value = values.get(name)
    if value is None or not value.strip():
        raise ConfigurationError(f"Variabile di configurazione obbligatoria mancante: {name}")
    return value


def load_telegram_config(values: Mapping[str, str | None]) -> TelegramConfig:
    return TelegramConfig(
        bot_token=required_value(values, "TELEGRAM_BOT_TOKEN"),
        chat_id=required_value(values, "TELEGRAM_CHAT_ID"),
    )


def load_monitor_config(values: Mapping[str, str | None]) -> MonitorConfig:
    interval_text = required_value(values, "CHECK_INTERVAL_MINUTES")
    try:
        interval = int(interval_text)
    except ValueError as exc:
        raise ConfigurationError(
            "CHECK_INTERVAL_MINUTES deve essere un numero intero positivo."
        ) from exc
    if interval <= 0:
        raise ConfigurationError(
            "CHECK_INTERVAL_MINUTES deve essere un numero intero positivo."
        )

    return MonitorConfig(
        username=required_value(values, "CUTAPP_USERNAME"),
        password=required_value(values, "CUTAPP_PASSWORD"),
        owner_code=required_value(values, "CUTAPP_OWNER_CODE"),
        shop_username=required_value(values, "CUTAPP_SHOP_USERNAME"),
        service=required_value(values, "CUTAPP_SERVICE"),
        telegram=load_telegram_config(values),
        check_interval_minutes=interval,
    )


def login_cutapp(session: requests.Session, config: MonitorConfig) -> str:
    payload = {
        "Username": config.username,
        "Password": config.password,
        "NotificationToken": "",
        "CodiceProprietario": config.owner_code,
        "DeviceId": None,
    }

    try:
        response = session.post(LOGIN_URL, json=payload, timeout=HTTP_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        raise CutAppError("Errore di rete durante il login CutApp.") from exc

    if not response.ok:
        raise CutAppError(f"Login CutApp fallito (HTTP {response.status_code}).")

    token = response.headers.get("X-Token", "").strip()
    if not token:
        raise CutAppError("Login CutApp riuscito, ma l'header X-Token è assente.")
    return token


def parse_cutapp_date(raw_value: Any) -> date:
    if not isinstance(raw_value, str) or not raw_value.strip():
        raise CutAppError("La risposta CutApp contiene una data non valida.")

    normalized = raw_value.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        return datetime.fromisoformat(normalized).date()
    except ValueError as exc:
        raise CutAppError("La risposta CutApp contiene una data non valida.") from exc


def fetch_available_dates(
    session: requests.Session, config: MonitorConfig, token: str
) -> list[date]:
    headers = {"Authorization": f"Bearer {token}"}
    payload = {
        "UsernameBottega": config.shop_username,
        "Servizi": [config.service],
    }

    try:
        response = session.post(
            CALENDAR_URL,
            headers=headers,
            json=payload,
            timeout=HTTP_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise CutAppError("Errore di rete durante il controllo del calendario CutApp.") from exc

    if not response.ok:
        raise CutAppError(
            f"Richiesta calendario CutApp fallita (HTTP {response.status_code})."
        )

    try:
        body = response.json()
    except (requests.exceptions.JSONDecodeError, ValueError) as exc:
        raise CutAppError("CutApp ha restituito JSON non valido.") from exc

    if not isinstance(body, dict) or not isinstance(body.get("giorni"), list):
        raise CutAppError("La risposta CutApp non contiene un campo 'giorni' valido.")

    # Ordina e rimuove eventuali duplicati ricevuti dall'API.
    return sorted({parse_cutapp_date(item) for item in body["giorni"]})


def send_telegram(config: TelegramConfig, text: str) -> None:
    url = f"https://api.telegram.org/bot{config.bot_token}/sendMessage"
    payload = {"chat_id": config.chat_id, "text": text}

    try:
        response = requests.post(url, data=payload, timeout=HTTP_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        # Non includere l'eccezione: l'URL potrebbe contenere il token del bot.
        raise TelegramError("Errore di rete durante l'invio del messaggio Telegram.") from exc

    if not response.ok:
        raise TelegramError(
            f"Invio del messaggio Telegram fallito (HTTP {response.status_code})."
        )

    try:
        result = response.json()
    except (requests.exceptions.JSONDecodeError, ValueError) as exc:
        raise TelegramError("Telegram ha restituito JSON non valido.") from exc
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise TelegramError("Telegram non ha confermato l'invio del messaggio.")


def format_date_it(value: date) -> str:
    return (
        f"{WEEKDAYS_IT[value.weekday()]} {value.day} "
        f"{MONTHS_IT[value.month]} {value.year}"
    )


def load_state() -> list[date] | None:
    """Restituisce None quando lo stato non è ancora stato inizializzato."""
    if not STATE_FILE.exists():
        return None

    try:
        with STATE_FILE.open("r", encoding="utf-8") as handle:
            content = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise StateError("Il file data/state.json non è leggibile o contiene JSON non valido.") from exc

    raw_dates = content.get("dates") if isinstance(content, dict) else None
    if not isinstance(raw_dates, list) or not all(isinstance(item, str) for item in raw_dates):
        raise StateError("Il file data/state.json non contiene uno stato valido.")

    try:
        return sorted({date.fromisoformat(item) for item in raw_dates})
    except ValueError as exc:
        raise StateError("Il file data/state.json contiene una data non valida.") from exc


def save_state(dates: list[date]) -> None:
    """Salva lo stato con sostituzione atomica di un file temporaneo."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    content = {
        "dates": [item.isoformat() for item in sorted(set(dates))],
        "last_successful_check": datetime.now(timezone.utc).isoformat(),
    }

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix="state.",
            suffix=".tmp",
            dir=STATE_DIR,
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            json.dump(content, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, STATE_FILE)
    except OSError as exc:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise StateError("Impossibile salvare data/state.json.") from exc


def build_initialization_message(dates: list[date]) -> str:
    if dates:
        last_date = format_date_it(max(dates))
    else:
        last_date = "nessuna data disponibile"
    return (
        "Monitor Cris Barbershop inizializzato. Controllo attivo. "
        f"Ultima data attualmente prenotabile: {last_date}."
    )


def build_new_dates_message(new_dates: list[date]) -> str:
    formatted_dates = "\n".join(f"• {format_date_it(item)}" for item in new_dates)
    return (
        "✂️ Nuove prenotazioni Cris Barbershop\n\n"
        "Sono state aperte queste giornate:\n"
        f"{formatted_dates}\n\n"
        "Apri subito l'app CutApp per scegliere l'orario."
    )


def check_once(config: MonitorConfig) -> None:
    """Esegue un controllo; lo stato cambia solo dopo una risposta CutApp valida."""
    with requests.Session() as session:
        token = login_cutapp(session, config)
        current_dates = fetch_available_dates(session, config, token)

    previous_dates = load_state()
    message: str | None = None

    if previous_dates is None:
        message = build_initialization_message(current_dates)
    else:
        new_dates = sorted(set(current_dates) - set(previous_dates))
        if new_dates:
            message = build_new_dates_message(new_dates)

    # Ogni risposta CutApp valida sostituisce lo stato precedente, incluse
    # eventuali rimozioni. In questo modo una riapertura viene rilevata.
    save_state(current_dates)

    if message is not None:
        send_telegram(config.telegram, message)

    if previous_dates is None:
        print(f"Monitor inizializzato: salvate {len(current_dates)} giornate.")
    elif message is not None:
        print("Nuove giornate rilevate e notifica Telegram inviata.")
    else:
        print(f"Controllo completato: nessuna nuova giornata ({len(current_dates)} disponibili).")


def run_watch(config: MonitorConfig) -> None:
    interval_seconds = config.check_interval_minutes * 60
    print(
        "Monitor continuo avviato. "
        f"Intervallo: {config.check_interval_minutes} minuti."
    )
    while True:
        try:
            check_once(config)
        except MonitorError as exc:
            print(f"Errore: {exc}", file=sys.stderr)

        try:
            time.sleep(interval_seconds)
        except KeyboardInterrupt:
            print("Monitor interrotto.")
            return


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Monitora le nuove giornate prenotabili di Cris Barbershop su CutApp."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--once", action="store_true", help="esegue un solo controllo")
    group.add_argument("--watch", action="store_true", help="controlla periodicamente")
    group.add_argument(
        "--test-telegram",
        action="store_true",
        help="invia soltanto un messaggio Telegram di prova",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_arguments()
    try:
        values = read_configuration()
        if args.test_telegram:
            telegram = load_telegram_config(values)
            send_telegram(
                telegram,
                "✅ Test monitor Cris Barbershop: notifiche Telegram configurate correttamente.",
            )
            print("Messaggio Telegram di prova inviato.")
            return 0

        config = load_monitor_config(values)
        if args.once:
            check_once(config)
        else:
            run_watch(config)
        return 0
    except MonitorError as exc:
        print(f"Errore: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Operazione interrotta.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
