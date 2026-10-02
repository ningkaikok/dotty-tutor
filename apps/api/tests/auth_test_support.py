from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager


@contextmanager
def environment(values: Mapping[str, str]) -> Iterator[None]:
    """Temporarily set environment configuration without mocking application code."""
    missing = object()
    previous: dict[str, str | object] = {key: os.environ.get(key, missing) for key in values}
    try:
        os.environ.update(values)
        yield
    finally:
        for key, value in previous.items():
            if value is missing:
                os.environ.pop(key, None)
            else:
                os.environ[key] = str(value)
