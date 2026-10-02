"""Compose the concrete background handlers used by the Worker process."""

from __future__ import annotations

from application.job_worker import TaskRegistry


def merge_registries(*registries: TaskRegistry) -> TaskRegistry:
    """Combine domain registries while rejecting accidental type collisions."""
    merged = TaskRegistry()
    for registry in registries:
        for name, handler in registry.items():
            merged.register(name, handler)
    return merged


__all__ = ["merge_registries"]
