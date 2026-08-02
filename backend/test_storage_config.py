from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from storage import build_postgres_url_from_env


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
