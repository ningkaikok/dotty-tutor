from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class OpenApiExportTests(unittest.TestCase):
    def test_export_does_not_require_runtime_database_configuration(self) -> None:
        repository_root = Path(__file__).resolve().parents[3]
        export_script = repository_root / "scripts" / "export-openapi.py"
        database_variables = (
            "DATABASE_URL",
            "POSTGRES_HOST",
            "POSTGRES_PORT",
            "POSTGRES_USER",
            "POSTGRES_PASSWORD",
            "POSTGRES_DB",
        )
        environment = os.environ.copy()
        for variable in database_variables:
            environment.pop(variable, None)

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "openapi.json"
            result = subprocess.run(
                [sys.executable, str(export_script), str(output)],
                cwd=repository_root,
                env=environment,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            schema = json.loads(output.read_text(encoding="utf-8"))
            self.assertIn("/api/health", schema["paths"])


if __name__ == "__main__":
    unittest.main()
