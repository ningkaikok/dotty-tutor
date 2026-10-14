"""Closed model-output schemas, separate from mutable HTTP editing contracts."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from domain.contracts.courseware import CoursewarePlanDraftOutput


def generation_schema(model: type[BaseModel], *, source: dict[str, Any] | None = None,
                      pages: list[dict[str, Any]] | None = None, asset_ids: list[str] | None = None) -> dict[str, Any]:
    """Build the strict structured-output subset without changing API contracts.

    Provider output requires finite object keys and all properties to be required.
    Coverage uses an entry list on the wire and is decoded by the draft contract;
    activity response hints have a finite type key, while selectionMode specifies
    choice interaction. Business bounds are still validated by Pydantic afterward.
    """
    schema = model.model_json_schema()
    if source is not None and pages is not None:
        # Constrain provenance before generation so a model cannot choose a
        # plausible but invented identifier and force a manual repair afterward.
        refs = schema.get("$defs", {}).get("CoursewareSourceRef", {}).get("properties", {})
        if refs:
            refs["sourceRevisionId"]["enum"] = [source["sourceRevisionId"]]
            refs["page"]["enum"] = sorted({int(page["page"]) for page in pages})
            for field, collection, key in [("sentenceIds", "sentences", "sentenceId"), ("regionIds", "regions", "regionId")]:
                identifiers = sorted({str(item[key]) for page in pages for item in page.get(collection, []) if item.get(key)})
                if identifiers:
                    refs[field]["items"]["enum"] = identifiers
                else:
                    refs[field]["maxItems"] = 0
            refs["assetId"] = {"type": ["string", "null"], "enum": [None, *(asset_ids or [])]}
        image_properties = schema.get("$defs", {}).get("CoursewareImageBlock", {}).get("properties", {})
        if image_properties and asset_ids:
            image_properties["assetId"]["enum"] = sorted(set(asset_ids))
    if model is CoursewarePlanDraftOutput:
        schema["properties"]["coverageMap"] = {
            "type": "array", "minItems": 1, "maxItems": 12,
            "items": {"type": "object", "properties": {
                "objectiveId": {"type": "string"},
                "sourceRefs": {"type": "array", "minItems": 1, "maxItems": 20,
                               "items": {"$ref": "#/$defs/CoursewareSourceRef"}},
            }},
        }

    def close(node: Any) -> None:
        if isinstance(node, list):
            for item in node:
                close(item)
            return
        if not isinstance(node, dict):
            return
        node.pop("default", None)
        node.pop("discriminator", None)
        if "oneOf" in node:
            node["anyOf"] = node.pop("oneOf")
        if node.get("type") == "object":
            if isinstance(node.get("additionalProperties"), dict):
                # The only remaining open model map is the bounded response hint.
                node["properties"] = {"type": {"type": "string"}}
            node["additionalProperties"] = False
            node["required"] = list(node.get("properties", {}))
            node.pop("minProperties", None)
            node.pop("maxProperties", None)
        for value in node.values():
            close(value)

    close(schema)
    return schema
