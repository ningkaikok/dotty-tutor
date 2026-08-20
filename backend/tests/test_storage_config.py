from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from persistence.database import build_postgres_url_from_env


class StorageConfigTests(unittest.TestCase):
    def test_builds_password_url_and_encodes_credentials(self) -> None:
        values = {
            "POSTGRES_HOST": "db.internal",
            "POSTGRES_PORT": "5433",
            "POSTGRES_USER": "dotty@app",
            "POSTGRES_PASSWORD": "p@ss/word#1",
            "POSTGRES_DB": "dotty tutor",
            "POSTGRES_SSLMODE": "require",
        }
        with patch.dict(os.environ, values, clear=True):
            self.assertEqual(
                build_postgres_url_from_env(),
                "postgresql+psycopg://dotty%40app:p%40ss%2Fword%231@db.internal:5433/dotty%20tutor?sslmode=require",
            )

    def test_keeps_socket_default_without_password(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(build_postgres_url_from_env(), "postgresql+psycopg:///dotty_tutor")

    def test_warns_when_falling_back_to_socket(self) -> None:
        with patch.dict(os.environ, {"POSTGRES_PORT": "15432"}, clear=True), \
                patch("persistence.database.log_event") as log_event:
            build_postgres_url_from_env()
        log_event.assert_called_once()
        self.assertEqual(log_event.call_args.args[0], "storage.postgres.socket_fallback")
        self.assertEqual(log_event.call_args.kwargs["level"], 30)

    def test_does_not_warn_when_password_present(self) -> None:
        with patch.dict(os.environ, {"POSTGRES_PASSWORD": "secret"}, clear=True), \
                patch("persistence.database.log_event") as log_event:
            build_postgres_url_from_env()
        log_event.assert_not_called()
