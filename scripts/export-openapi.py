#!/usr/bin/env python3
"""Export the FastAPI application schema without starting a server.

The schema is intentionally produced by the application itself so the generated
frontend types follow the response models registered on the real routes.  This
script only writes the requested output file; it never includes runtime secrets
or model prompt contents.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "apps" / "api"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

# OpenAPI generation imports the fully assembled application, whose stores require an
# explicit PostgreSQL URL.  Engine construction is lazy and does not connect, so use a
# deliberately unreachable schema-only URL when the caller did not provide runtime
# configuration.  This keeps CI type generation independent from a shared database
# without reintroducing a SQLite fallback.
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://openapi:openapi@127.0.0.1:1/openapi",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="path for the generated OpenAPI JSON")
    args = parser.parse_args()

    from app import app  # imported after PYTHONPATH setup to match uvicorn app:app

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(app.openapi(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
