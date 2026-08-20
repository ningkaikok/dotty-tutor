"""Worker CLI entry point for ``python -m worker`` from the backend directory."""

from application.job_worker import *  # noqa: F401,F403
from application.job_worker import main


if __name__ == "__main__":  # pragma: no cover
    main()
