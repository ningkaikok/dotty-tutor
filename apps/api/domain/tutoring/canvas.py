"""Deterministic validation for the first structured math-canvas scene."""

from __future__ import annotations

from math import hypot
from typing import Any


def normalize_point_placement(value: Any) -> dict[str, Any] | None:
    """Return a bounded, replayable point-placement state or ``None``."""
    if not isinstance(value, dict) or value.get("kind") != "point-placement":
        return None
    bounds = {}
    for key, default in (("xMin", -10.0), ("xMax", 10.0), ("yMin", -10.0), ("yMax", 10.0)):
        try:
            bounds[key] = float(value.get(key, default))
        except (TypeError, ValueError):
            bounds[key] = default
    if bounds["xMin"] >= bounds["xMax"] or bounds["yMin"] >= bounds["yMax"]:
        return None
    points: list[dict[str, Any]] = []
    for raw in value.get("points", []) if isinstance(value.get("points"), list) else []:
        if not isinstance(raw, dict):
            continue
        try:
            point = {
                "id": str(raw.get("id", "student"))[:64],
                "x": max(bounds["xMin"], min(bounds["xMax"], float(raw["x"]))),
                "y": max(bounds["yMin"], min(bounds["yMax"], float(raw["y"]))),
            }
        except (KeyError, TypeError, ValueError):
            continue
        points.append(point)
    operations = [item for item in value.get("operations", []) if isinstance(item, dict)][:200]
    return {
        "schemaVersion": "math-canvas-v1",
        "kind": "point-placement",
        **bounds,
        "points": points[:32],
        "operations": operations,
    }


def evaluate_point_placement(
    canvas_state: Any,
    *,
    target: dict[str, Any],
) -> dict[str, Any] | None:
    """Evaluate the point against a target using a server-owned tolerance."""
    state = normalize_point_placement(canvas_state)
    if state is None:
        return None
    try:
        target_x = float(target["x"])
        target_y = float(target["y"])
        tolerance = max(0.001, min(float(target.get("tolerance", 0.25)), 5.0))
    except (KeyError, TypeError, ValueError):
        return None
    student = next((item for item in state["points"] if item["id"] == "student"), None)
    if student is None and state["points"]:
        student = state["points"][0]
    if student is None:
        return {
            "assessment": "incorrect",
            "distance": None,
            "tolerance": tolerance,
            "strategy": "point-placement",
            "submitted": None,
            "target": {"x": target_x, "y": target_y},
        }
    distance = hypot(student["x"] - target_x, student["y"] - target_y)
    return {
        "assessment": "correct" if distance <= tolerance else "incorrect",
        "distance": round(distance, 4),
        "tolerance": tolerance,
        "strategy": "point-placement",
        "submitted": {"x": student["x"], "y": student["y"]},
        "target": {"x": target_x, "y": target_y},
    }
