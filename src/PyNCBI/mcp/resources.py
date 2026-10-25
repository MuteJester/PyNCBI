"""MCP resources and resource templates (schemas, vocabulary, per-study views)."""

from __future__ import annotations

import re
from typing import Any

from PyNCBI.core.serialization import json_dumps
from PyNCBI.core.tables import csv_text

MCP_RESOURCES: list[dict[str, Any]] = [
    {
        "uri": "pyncbi://schema/study",
        "name": "StudyRecord JSON Schema",
        "title": "Study record contract",
        "description": "JSON Schema (draft 2020-12) of the versioned study record returned by ncbi_metadata.",
        "mimeType": "application/json",
    },
    {
        "uri": "pyncbi://schema/sample",
        "name": "SampleRecord JSON Schema",
        "title": "Sample record contract",
        "description": "JSON Schema of a single sample: raw characteristics plus harmonized values with provenance.",
        "mimeType": "application/json",
    },
    {
        "uri": "pyncbi://vocabulary",
        "name": "Controlled vocabulary",
        "title": "Harmonization vocabulary",
        "description": "Canonical sample fields (sex, age, tissue, disease, ...), their synonyms and value normalizers.",
        "mimeType": "application/json",
    },
]


MCP_RESOURCE_TEMPLATES: list[dict[str, Any]] = [
    {
        "uriTemplate": "pyncbi://study/{gse_id}/overview",
        "name": "Study overview",
        "description": "Markdown overview of a GEO series: design, cohort, harmonized fields, quality and files.",
        "mimeType": "text/markdown",
    },
    {
        "uriTemplate": "pyncbi://study/{gse_id}/samples",
        "name": "Harmonized sample sheet",
        "description": "CSV sample sheet with canonical columns (sex, age, disease, ...) and raw:<key> passthroughs.",
        "mimeType": "text/csv",
    },
    {
        "uriTemplate": "pyncbi://study/{gse_id}/record",
        "name": "Study record",
        "description": "The full versioned StudyRecord as JSON (same shape as ncbi_metadata).",
        "mimeType": "application/json",
    },
    {
        "uriTemplate": "pyncbi://platform/{gpl_id}/table",
        "name": "Platform annotation table",
        "description": "First rows of a GPL platform table (probe -> gene symbol / Entrez ID) as CSV.",
        "mimeType": "text/csv",
    },
]


RESOURCE_TABLE_ROW_LIMIT = 200


_STUDY_URI_RE = re.compile(
    r"^pyncbi://study/(?P<gse>GSE\d+)/(?P<part>overview|samples|record)$", re.I
)


_PLATFORM_URI_RE = re.compile(r"^pyncbi://platform/(?P<gpl>GPL\d+)/table$", re.I)


_SCHEMA_URI_RE = re.compile(r"^pyncbi://schema/(?P<name>study|sample)$", re.I)


def read_resource(uri: str) -> dict[str, Any]:
    """Resolve an MCP resource URI into `{uri, mimeType, text}`.

    Supported: ``pyncbi://schema/{study|sample}``, ``pyncbi://vocabulary``,
    ``pyncbi://study/{GSE}/{overview|samples|record}``, ``pyncbi://platform/{GPL}/table``.
    """
    from PyNCBI.metadata import build_study_record, json_schema, vocabulary

    target = str(uri).strip()

    schema_match = _SCHEMA_URI_RE.match(target)
    if schema_match:
        name = schema_match.group("name").lower()
        return {
            "uri": target,
            "mimeType": "application/json",
            "text": json_dumps(json_schema(name), indent=2),
        }

    if target.lower() == "pyncbi://vocabulary":
        fields = {k: v.to_dict() for k, v in vocabulary.fields().items()}
        return {
            "uri": target,
            "mimeType": "application/json",
            "text": json_dumps({"field_count": len(fields), "fields": fields}, indent=2),
        }

    study_match = _STUDY_URI_RE.match(target)
    if study_match:
        gse_id = study_match.group("gse").upper()
        part = study_match.group("part").lower()
        record = build_study_record(gse_id, check_file_sizes=False)
        if part == "overview":
            return {"uri": target, "mimeType": "text/markdown", "text": record.to_markdown()}
        if part == "samples":
            return {
                "uri": target,
                "mimeType": "text/csv",
                "text": csv_text(record.harmonized_table()),
            }
        return {
            "uri": target,
            "mimeType": "application/json",
            "text": json_dumps(record.to_dict(), indent=2),
        }

    platform_match = _PLATFORM_URI_RE.match(target)
    if platform_match:
        from PyNCBI.domains.annotation import fetch_gpl_table

        gpl_id = platform_match.group("gpl").upper()
        table = fetch_gpl_table(gpl_id, limit=RESOURCE_TABLE_ROW_LIMIT)
        return {"uri": target, "mimeType": "text/csv", "text": csv_text(table)}

    raise ValueError(
        f"Unsupported resource URI '{uri}'. Known forms: pyncbi://schema/study, pyncbi://schema/sample, "
        "pyncbi://vocabulary, pyncbi://study/{GSE}/overview|samples|record, pyncbi://platform/{GPL}/table"
    )
