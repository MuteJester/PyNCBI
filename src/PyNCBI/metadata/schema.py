"""Versioned metadata schema for PyNCBI: study and sample records with provenance.

Every surface (Python, CLI ``--json``, MCP, on-disk bundles) serializes these records with
the same shape. Raw NCBI values are always kept verbatim next to normalized values, and each
normalized value carries its provenance (source, source key, rule, confidence).
"""

from __future__ import annotations

import hashlib
import typing
from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
from typing import Any

from PyNCBI.core.serialization import json_dumps
from PyNCBI.core.tables import Table, from_records

SCHEMA_VERSION = "1.0"
JSON_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"

CONFIDENCE_LEVELS: tuple[str, ...] = ("exact", "inferred", "verbatim", "unmapped", "missing")


@dataclass
class Provenance:
    """Where a normalized value came from and how it was derived."""

    source: str = "geo:characteristics_ch1"
    source_key: str = ""
    rule: str = ""
    confidence: str = "verbatim"

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "source_key": self.source_key,
            "rule": self.rule,
            "confidence": self.confidence,
        }


@dataclass
class NormalizedValue:
    """A harmonized value plus the raw string it was derived from."""

    value: Any = None
    raw: str = ""
    unit: str | None = None
    confidence: str = "verbatim"
    rule: str = ""
    source_key: str = ""
    source: str = "geo:characteristics_ch1"
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def provenance(self) -> Provenance:
        return Provenance(
            source=self.source,
            source_key=self.source_key,
            rule=self.rule,
            confidence=self.confidence,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "raw": self.raw,
            "unit": self.unit,
            "extra": dict(self.extra),
            "provenance": self.provenance.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NormalizedValue:
        prov = data.get("provenance") or {}
        return cls(
            value=data.get("value"),
            raw=str(data.get("raw", "")),
            unit=data.get("unit"),
            confidence=str(prov.get("confidence", data.get("confidence", "verbatim"))),
            rule=str(prov.get("rule", "")),
            source_key=str(prov.get("source_key", "")),
            source=str(prov.get("source", "geo:characteristics_ch1")),
            extra=dict(data.get("extra") or {}),
        )


@dataclass
class QualityIssue:
    """A typed data-quality finding."""

    code: str
    severity: str  # error | warning | info
    message: str
    count: int | None = None
    field: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "count": self.count,
            "field": self.field,
        }


@dataclass
class QualityReport:
    """Completeness score (0-100) and issues for a study record."""

    score: int = 100
    issues: list[QualityIssue] = field(default_factory=list)
    completeness: dict[str, float] = field(default_factory=dict)
    assessed_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "issues": [i.to_dict() for i in self.issues],
            "completeness": dict(self.completeness),
            "assessed_at": self.assessed_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> QualityReport:
        return cls(
            score=int(data.get("score", 0)),
            issues=[
                QualityIssue(
                    **{
                        k: v
                        for k, v in i.items()
                        if k in ("code", "severity", "message", "count", "field")
                    }
                )
                for i in data.get("issues", [])
            ],
            completeness=dict(data.get("completeness") or {}),
            assessed_at=str(data.get("assessed_at", "")),
        )


@dataclass
class SampleRecord:
    """One GEO sample (GSM) with raw characteristics, harmonized values, and cross-database links."""

    gsm_id: str
    title: str = ""
    organism: str = ""
    platform_id: str = ""
    source_name: str = ""
    molecule: str = ""
    library_strategy: str = ""
    characteristics: dict[str, str] = field(default_factory=dict)
    harmonized: dict[str, NormalizedValue] = field(default_factory=dict)
    supplementary_files: list[str] = field(default_factory=list)
    links: dict[str, str] = field(
        default_factory=dict
    )  # biosample, sra_experiment, sra_sample, series
    data_row_count: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_type": "sample",
            "schema_version": SCHEMA_VERSION,
            "gsm_id": self.gsm_id,
            "title": self.title,
            "organism": self.organism,
            "platform_id": self.platform_id,
            "source_name": self.source_name,
            "molecule": self.molecule,
            "library_strategy": self.library_strategy,
            "characteristics": dict(self.characteristics),
            "harmonized": {k: v.to_dict() for k, v in self.harmonized.items()},
            "supplementary_files": list(self.supplementary_files),
            "links": dict(self.links),
            "data_row_count": self.data_row_count,
            "extra": dict(self.extra),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SampleRecord:
        return cls(
            gsm_id=str(data.get("gsm_id", "")),
            title=str(data.get("title", "")),
            organism=str(data.get("organism", "")),
            platform_id=str(data.get("platform_id", "")),
            source_name=str(data.get("source_name", "")),
            molecule=str(data.get("molecule", "")),
            library_strategy=str(data.get("library_strategy", "")),
            characteristics={
                str(k): str(v) for k, v in (data.get("characteristics") or {}).items()
            },
            harmonized={
                str(k): NormalizedValue.from_dict(v)
                for k, v in (data.get("harmonized") or {}).items()
            },
            supplementary_files=[str(u) for u in data.get("supplementary_files") or []],
            links={str(k): str(v) for k, v in (data.get("links") or {}).items()},
            data_row_count=int(data.get("data_row_count", 0) or 0),
            extra=dict(data.get("extra") or {}),
        )


@dataclass
class StudyRecord:
    """One GEO series (GSE) with its samples, files, links, harmonization summary, and quality."""

    gse_id: str
    title: str = ""
    summary: str = ""
    overall_design: str = ""
    organisms: list[str] = field(default_factory=list)
    platforms: list[str] = field(default_factory=list)
    experiment_types: list[str] = field(default_factory=list)
    pubmed_ids: list[str] = field(default_factory=list)
    bioproject_id: str = ""
    superseries_id: str = ""
    subseries_ids: list[str] = field(default_factory=list)
    declared_sample_count: int = 0
    samples: list[SampleRecord] = field(default_factory=list)
    files: list[dict[str, Any]] = field(default_factory=list)
    links: dict[str, Any] = field(default_factory=dict)  # bioproject, sra_study, pubmed, pmc, ...
    harmonization: dict[str, Any] = field(default_factory=dict)
    quality: QualityReport | None = None
    retrieved_at: str = ""
    source_versions: dict[str, str] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)

    # ---- derived views ----

    @property
    def sample_count(self) -> int:
        return len(self.samples)

    def sample_table(self) -> Table:
        """Raw sample table: core fields + original characteristics columns, indexed by GSM."""
        rows = []
        for s in self.samples:
            row: dict[str, Any] = {
                "gsm_id": s.gsm_id,
                "title": s.title,
                "organism": s.organism,
                "platform_id": s.platform_id,
                "source_name": s.source_name,
                "library_strategy": s.library_strategy,
            }
            row.update(s.characteristics)
            row.update({f"link:{k}": v for k, v in s.links.items()})
            rows.append(row)
        return from_records(rows)

    def harmonized_table(self) -> Table:
        """Harmonized sample table (canonical columns + derived + raw:<key> for unmapped)."""
        rows = []
        for s in self.samples:
            row: dict[str, Any] = {"gsm_id": s.gsm_id}
            for key, nv in s.harmonized.items():
                row[key] = nv.value
                if nv.unit is not None:
                    row[f"{key}_unit"] = nv.unit
                for ek, ev in nv.extra.items():
                    row[ek] = ev
            rows.append(row)
        return from_records(rows)

    def provenance_table(self) -> Table:
        rows = []
        for s in self.samples:
            for key, nv in s.harmonized.items():
                p = nv.provenance
                rows.append(
                    {
                        "gsm_id": s.gsm_id,
                        "field": key,
                        "source": p.source,
                        "source_key": p.source_key,
                        "raw": nv.raw,
                        "value": nv.value,
                        "unit": nv.unit,
                        "rule": p.rule,
                        "confidence": p.confidence,
                    }
                )
        return from_records(
            rows,
            columns=[
                "gsm_id",
                "field",
                "source",
                "source_key",
                "raw",
                "value",
                "unit",
                "rule",
                "confidence",
            ],
        )

    # ---- serialization ----

    def to_dict(self, *, include_samples: bool = True) -> dict[str, Any]:
        data: dict[str, Any] = {
            "record_type": "study",
            "schema_version": SCHEMA_VERSION,
            "gse_id": self.gse_id,
            "title": self.title,
            "summary": self.summary,
            "overall_design": self.overall_design,
            "organisms": list(self.organisms),
            "platforms": list(self.platforms),
            "experiment_types": list(self.experiment_types),
            "pubmed_ids": list(self.pubmed_ids),
            "bioproject_id": self.bioproject_id,
            "superseries_id": self.superseries_id,
            "subseries_ids": list(self.subseries_ids),
            "declared_sample_count": self.declared_sample_count,
            "sample_count": self.sample_count,
            "samples": [s.to_dict() for s in self.samples] if include_samples else [],
            "files": [dict(f) for f in self.files],
            "links": dict(self.links),
            "harmonization": dict(self.harmonization),
            "quality": self.quality.to_dict() if self.quality is not None else None,
            "retrieved_at": self.retrieved_at,
            "source_versions": dict(self.source_versions),
            "extra": dict(self.extra),
        }
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StudyRecord:
        return cls(
            gse_id=str(data.get("gse_id", "")),
            title=str(data.get("title", "")),
            summary=str(data.get("summary", "")),
            overall_design=str(data.get("overall_design", "")),
            organisms=[str(x) for x in data.get("organisms") or []],
            platforms=[str(x) for x in data.get("platforms") or []],
            experiment_types=[str(x) for x in data.get("experiment_types") or []],
            pubmed_ids=[str(x) for x in data.get("pubmed_ids") or []],
            bioproject_id=str(data.get("bioproject_id", "")),
            superseries_id=str(data.get("superseries_id", "")),
            subseries_ids=[str(x) for x in data.get("subseries_ids") or []],
            declared_sample_count=int(data.get("declared_sample_count", 0) or 0),
            samples=[SampleRecord.from_dict(s) for s in data.get("samples") or []],
            files=[dict(f) for f in data.get("files") or []],
            links=dict(data.get("links") or {}),
            harmonization=dict(data.get("harmonization") or {}),
            quality=QualityReport.from_dict(data["quality"]) if data.get("quality") else None,
            retrieved_at=str(data.get("retrieved_at", "")),
            source_versions=dict(data.get("source_versions") or {}),
            extra=dict(data.get("extra") or {}),
        )

    def canonical_json(self) -> str:
        """Deterministic JSON of the NCBI-derived content (excludes retrieval time and quality)."""
        data = self.to_dict()
        for volatile in ("retrieved_at", "quality"):
            data.pop(volatile, None)
        return json_dumps(data, sort_keys=True, separators=(",", ":"))

    def content_hash(self) -> str:
        """SHA-256 of the canonical JSON; equal hashes mean identical NCBI content."""
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    # ---- human views ----

    def to_markdown(self, *, max_samples: int = 15) -> str:
        from PyNCBI.metadata.serving import study_markdown

        return study_markdown(self, max_samples=max_samples)

    def to_text(self) -> str:
        return self.to_markdown().replace("`", "").replace("**", "")

    def save(self, output_dir: str | Any) -> dict[str, Any]:
        from PyNCBI.metadata.store import save_study_record

        return save_study_record(self, output_dir)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# JSON Schema generation (stdlib only)
# ---------------------------------------------------------------------------

_SCHEMA_DESCRIPTIONS: dict[str, str] = {
    "study": "A GEO series with its samples, downloadable files, cross-database links, harmonization summary and quality report.",
    "sample": "A GEO sample with raw characteristics, harmonized values (with provenance) and cross-database links.",
}


def _type_schema(tp: Any) -> dict[str, Any]:
    origin = typing.get_origin(tp)
    args = typing.get_args(tp)
    if tp is str:
        return {"type": "string"}
    if tp is int:
        return {"type": "integer"}
    if tp is float:
        return {"type": "number"}
    if tp is bool:
        return {"type": "boolean"}
    if tp is Any:
        return {}
    if origin in (list,):
        return {"type": "array", "items": _type_schema(args[0]) if args else {}}
    if origin in (dict,):
        return {
            "type": "object",
            "additionalProperties": _type_schema(args[1]) if len(args) == 2 else {},
        }
    if origin is typing.Union or str(origin) == "<class 'types.UnionType'>":
        non_none = [a for a in args if a is not type(None)]
        base = (
            _type_schema(non_none[0])
            if len(non_none) == 1
            else {"anyOf": [_type_schema(a) for a in non_none]}
        )
        if len(non_none) < len(args):
            return (
                {"anyOf": [base, {"type": "null"}]}
                if "anyOf" not in base
                else {"anyOf": [*base["anyOf"], {"type": "null"}]}
            )
        return base
    if tp in (NormalizedValue, QualityReport, QualityIssue, Provenance, SampleRecord):
        return {"$ref": f"#/$defs/{tp.__name__}"}
    return {}


def _dataclass_schema(
    cls: type, *, extra_props: dict[str, Any] | None = None, required: list[str] | None = None
) -> dict[str, Any]:
    hints = typing.get_type_hints(cls)
    props: dict[str, Any] = {}
    for f in fields(cls):
        props[f.name] = _type_schema(hints[f.name])
    if extra_props:
        props.update(extra_props)
    schema: dict[str, Any] = {"type": "object", "properties": props, "additionalProperties": False}
    if required:
        schema["required"] = required
    return schema


def _normalized_value_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "value": {},
            "raw": {"type": "string"},
            "unit": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            "extra": {"type": "object"},
            "provenance": {"$ref": "#/$defs/Provenance"},
        },
        "required": ["value", "raw", "provenance"],
        "additionalProperties": False,
    }


def json_schema(name: str = "study") -> dict[str, Any]:
    """Return the JSON Schema (draft 2020-12) for the ``study`` or ``sample`` record."""
    kind = name.strip().lower()
    if kind not in ("study", "sample"):
        raise ValueError("name must be 'study' or 'sample'")
    defs: dict[str, Any] = {
        "Provenance": {
            **_dataclass_schema(Provenance),
            "properties": {
                "source": {"type": "string"},
                "source_key": {"type": "string"},
                "rule": {"type": "string"},
                "confidence": {"type": "string", "enum": list(CONFIDENCE_LEVELS)},
            },
        },
        "NormalizedValue": _normalized_value_schema(),
        "QualityIssue": {
            **_dataclass_schema(QualityIssue),
            "properties": {
                "code": {"type": "string"},
                "severity": {"type": "string", "enum": ["error", "warning", "info"]},
                "message": {"type": "string"},
                "count": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
                "field": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            },
        },
        "QualityReport": {
            "type": "object",
            "properties": {
                "score": {"type": "integer", "minimum": 0, "maximum": 100},
                "issues": {"type": "array", "items": {"$ref": "#/$defs/QualityIssue"}},
                "completeness": {"type": "object", "additionalProperties": {"type": "number"}},
                "assessed_at": {"type": "string"},
            },
            "additionalProperties": False,
        },
    }
    sample_props = _dataclass_schema(SampleRecord)["properties"]
    sample_props["harmonized"] = {
        "type": "object",
        "additionalProperties": {"$ref": "#/$defs/NormalizedValue"},
    }
    sample_schema = {
        "type": "object",
        "properties": {
            "record_type": {"const": "sample"},
            "schema_version": {"const": SCHEMA_VERSION},
            **sample_props,
        },
        "required": ["record_type", "schema_version", "gsm_id"],
        "additionalProperties": False,
    }
    if kind == "sample":
        return {
            "$schema": JSON_SCHEMA_DIALECT,
            "$id": f"https://pyncbi.readthedocs.io/schemas/{SCHEMA_VERSION}/sample.schema.json",
            "title": "PyNCBI SampleRecord",
            "description": _SCHEMA_DESCRIPTIONS["sample"],
            **sample_schema,
            "$defs": {k: defs[k] for k in ("Provenance", "NormalizedValue")},
        }
    defs["SampleRecord"] = sample_schema
    study_props = _dataclass_schema(StudyRecord)["properties"]
    study_props.pop("samples", None)
    study_props.pop("quality", None)
    study_props["samples"] = {"type": "array", "items": {"$ref": "#/$defs/SampleRecord"}}
    study_props["quality"] = {"anyOf": [{"$ref": "#/$defs/QualityReport"}, {"type": "null"}]}
    study_props["files"] = {"type": "array", "items": {"type": "object"}}
    return {
        "$schema": JSON_SCHEMA_DIALECT,
        "$id": f"https://pyncbi.readthedocs.io/schemas/{SCHEMA_VERSION}/study.schema.json",
        "title": "PyNCBI StudyRecord",
        "description": _SCHEMA_DESCRIPTIONS["study"],
        "type": "object",
        "properties": {
            "record_type": {"const": "study"},
            "schema_version": {"const": SCHEMA_VERSION},
            "sample_count": {"type": "integer"},
            **study_props,
        },
        "required": ["record_type", "schema_version", "gse_id", "samples"],
        "additionalProperties": False,
        "$defs": defs,
    }
