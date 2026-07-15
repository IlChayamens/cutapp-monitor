from __future__ import annotations

import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path
from unittest.mock import patch

import monitor


class MonitorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.state_directory = Path(self.temporary_directory.name) / "data"
        self.state_file = self.state_directory / "state.json"
        self.path_patches = (
            patch.object(monitor, "STATE_DIR", self.state_directory),
            patch.object(monitor, "STATE_FILE", self.state_file),
        )
        for active_patch in self.path_patches:
            active_patch.start()

        self.config = monitor.MonitorConfig(
            username="test-user",
            password="test-password",
            owner_code="valicenti",
            shop_username="CrisBarbershop",
            service="TAGLIO CAPELLI",
            telegram=monitor.TelegramConfig(
                bot_token="test-bot-token",
                chat_id="test-chat-id",
            ),
            check_interval_minutes=10,
        )

    def tearDown(self) -> None:
        for active_patch in reversed(self.path_patches):
            active_patch.stop()
        self.temporary_directory.cleanup()

    def test_format_date_in_italian_without_system_locale(self) -> None:
        self.assertEqual(
            monitor.format_date_it(date(2026, 8, 11)),
            "martedì 11 agosto 2026",
        )
        self.assertEqual(
            monitor.format_date_it(date(2026, 8, 12)),
            "mercoledì 12 agosto 2026",
        )

    def test_configuration_from_environment_without_dotenv(self) -> None:
        environment = {
            "CUTAPP_USERNAME": "example-user",
            "CUTAPP_PASSWORD": "example-password",
            "CUTAPP_OWNER_CODE": "valicenti",
            "CUTAPP_SHOP_USERNAME": "CrisBarbershop",
            "CUTAPP_SERVICE": "TAGLIO CAPELLI",
            "TELEGRAM_BOT_TOKEN": "example-bot-token",
            "TELEGRAM_CHAT_ID": "example-chat-id",
            "CHECK_INTERVAL_MINUTES": "10",
        }
        missing_env_file = Path(self.temporary_directory.name) / ".env"

        with (
            patch.object(monitor, "ENV_FILE", missing_env_file),
            patch.dict(os.environ, environment, clear=True),
        ):
            values = monitor.read_configuration()
            config = monitor.load_monitor_config(values)

        self.assertEqual(config.username, "example-user")
        self.assertEqual(config.owner_code, "valicenti")
        self.assertEqual(config.check_interval_minutes, 10)

    def test_missing_required_configuration_names_the_variable(self) -> None:
        with self.assertRaisesRegex(
            monitor.ConfigurationError,
            "CUTAPP_USERNAME",
        ):
            monitor.required_value({}, "CUTAPP_USERNAME")

    def test_initialization_new_dates_no_duplicates_and_reopening(self) -> None:
        current_dates = [date(2026, 8, 4), date(2026, 8, 5)]
        sent_messages: list[str] = []

        def fake_calendar(*_args: object) -> list[date]:
            return list(current_dates)

        def fake_telegram(_config: monitor.TelegramConfig, text: str) -> None:
            sent_messages.append(text)

        with (
            patch.object(monitor, "login_cutapp", return_value="opaque-token"),
            patch.object(monitor, "fetch_available_dates", side_effect=fake_calendar),
            patch.object(monitor, "send_telegram", side_effect=fake_telegram),
            redirect_stdout(io.StringIO()),
        ):
            monitor.check_once(self.config)
            self.assertEqual(len(sent_messages), 1)
            self.assertIn("inizializzato", sent_messages[-1])
            self.assertEqual(monitor.load_state(), current_dates)

            current_dates.append(date(2026, 8, 11))
            monitor.check_once(self.config)
            self.assertEqual(len(sent_messages), 2)
            self.assertIn("• martedì 11 agosto 2026", sent_messages[-1])

            monitor.check_once(self.config)
            self.assertEqual(len(sent_messages), 2)

            current_dates[:] = [date(2026, 8, 11)]
            monitor.check_once(self.config)
            self.assertEqual(len(sent_messages), 2)
            self.assertEqual(monitor.load_state(), current_dates)

            current_dates.append(date(2026, 8, 5))
            monitor.check_once(self.config)
            self.assertEqual(len(sent_messages), 3)
            self.assertIn("• mercoledì 5 agosto 2026", sent_messages[-1])

        self.assertEqual(list(self.state_directory.glob("state.*.tmp")), [])

    def test_cutapp_failure_does_not_overwrite_state(self) -> None:
        original_dates = [date(2026, 8, 4)]
        monitor.save_state(original_dates)

        with patch.object(
            monitor,
            "login_cutapp",
            side_effect=monitor.CutAppError("Errore CutApp simulato."),
        ):
            with self.assertRaises(monitor.CutAppError):
                monitor.check_once(self.config)

        self.assertEqual(monitor.load_state(), original_dates)


if __name__ == "__main__":
    unittest.main()
