"""Controlled vocabulary for sample characteristics (conservative, provenance-first).

A characteristic key is mapped to a canonical field only when it matches a registered
synonym after light normalization (case, underscores, parenthetical units). Values are
normalized only for fields with an unambiguous value space (``sex``), parsed for quantities
(``age``, ``time_point``), and otherwise kept verbatim. Every result is a
:class:`~PyNCBI.metadata.schema.NormalizedValue` carrying the raw string, rule id and
confidence. The registry is extensible at runtime (``register_field`` / ``register_synonym``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from PyNCBI.metadata.schema import NormalizedValue

MISSING_TOKENS: frozenset[str] = frozenset(
    {
        "",
        "na",
        "n/a",
        "nan",
        "none",
        "null",
        "unknown",
        "not available",
        "not reported",
        "not applicable",
        "--",
        "-",
        "?",
        "not specified",
        "unk",
        "missing",
        "nd",
        "n.d.",
    }
)


@dataclass
class CanonicalField:
    """A canonical sample attribute with its synonyms and value normalizer."""

    name: str
    description: str
    synonyms: list[str] = field(default_factory=list)
    normalizer: str = "verbatim"  # verbatim | sex | quantity | disease
    dtype: str = "string"  # string | number | boolean

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "synonyms": list(self.synonyms),
            "normalizer": self.normalizer,
            "dtype": self.dtype,
        }


_DEFAULT_FIELDS: tuple[CanonicalField, ...] = (
    CanonicalField(
        "sex",
        "Biological sex of the donor/organism (female, male, mixed)",
        [
            "sex",
            "gender",
            "sex (male/female)",
            "donor sex",
            "patient sex",
            "biological sex",
            "sex of donor",
        ],
        "sex",
    ),
    CanonicalField(
        "age",
        "Age at sampling; parsed with unit (year, month, week, day, embryonic_day) and age_years",
        [
            "age",
            "age (years)",
            "age (yrs)",
            "age (y)",
            "age in years",
            "age at diagnosis",
            "age at sampling",
            "age at collection",
            "donor age",
            "patient age",
            "age (months)",
            "age (weeks)",
            "age (days)",
            "age at death",
            "age_years",
            "age years",
        ],
        "quantity",
        "number",
    ),
    CanonicalField(
        "tissue",
        "Tissue or anatomical site",
        [
            "tissue",
            "tissue type",
            "tissue source",
            "tissue/cell type",
            "tissue origin",
            "organ",
            "anatomical site",
            "body site",
            "biopsy site",
            "tissue region",
        ],
        "verbatim",
    ),
    CanonicalField(
        "cell_type",
        "Cell type or population",
        ["cell type", "celltype", "cell population", "cell subtype", "sorted cell type"],
        "verbatim",
    ),
    CanonicalField(
        "cell_line",
        "Cell line name",
        ["cell line", "cellline", "cell line name"],
        "verbatim",
    ),
    CanonicalField(
        "disease",
        "Disease, condition or diagnosis (verbatim label; is_control flag derived)",
        [
            "disease",
            "disease state",
            "disease status",
            "disease_state",
            "disease_status",
            "diagnosis",
            "condition",
            "health status",
            "health state",
            "clinical status",
            "disease type",
            "diagnosis group",
            "patient's condition",
            "patient condition",
            "disease group",
            "phenotype",
        ],
        "disease",
    ),
    CanonicalField(
        "treatment",
        "Treatment, agent, drug or exposure",
        [
            "treatment",
            "treated with",
            "treatment group",
            "agent",
            "drug",
            "compound",
            "exposure",
            "stimulation",
            "stimulus",
            "intervention",
            "therapy",
            "drug treatment",
        ],
        "verbatim",
    ),
    CanonicalField(
        "time_point",
        "Time point relative to treatment, onset or collection (parsed with unit)",
        [
            "time point",
            "timepoint",
            "time (h)",
            "time (hours)",
            "time (days)",
            "time post treatment",
            "time after treatment",
            "days post symptom onset",
            "days since symptom onset",
            "days post infection",
            "hours post infection",
            "days post treatment",
            "time post infection",
            "sampling time",
        ],
        "quantity",
        "number",
    ),
    CanonicalField(
        "genotype",
        "Genotype or genetic variation",
        ["genotype", "genotype/variation", "genetic background", "mutation", "variant", "allele"],
        "verbatim",
    ),
    CanonicalField(
        "strain",
        "Organism strain or breed",
        [
            "strain",
            "strain background",
            "mouse strain",
            "rat strain",
            "background strain",
            "breed",
            "strain/background",
        ],
        "verbatim",
    ),
    CanonicalField(
        "developmental_stage",
        "Developmental or life stage",
        [
            "developmental stage",
            "development stage",
            "dev stage",
            "life stage",
            "embryonic stage",
            "growth stage",
        ],
        "verbatim",
    ),
    CanonicalField(
        "sample_type",
        "Sample material type (e.g. biopsy, cell culture, FFPE)",
        [
            "sample type",
            "sample_type",
            "specimen type",
            "material type",
            "biomaterial",
            "sample source",
        ],
        "verbatim",
    ),
    CanonicalField(
        "batch",
        "Processing batch or run",
        ["batch", "processing batch", "sequencing batch", "array batch"],
        "verbatim",
    ),
    CanonicalField(
        "organism",
        "Organism of the sample (when given as a characteristic)",
        ["organism", "species", "organism name", "taxon"],
        "verbatim",
    ),
    CanonicalField(
        "ethnicity",
        "Self-reported ethnicity or ancestry",
        ["ethnicity", "race", "ancestry", "race/ethnicity", "ethnic group"],
        "verbatim",
    ),
    CanonicalField(
        "bmi", "Body mass index", ["bmi", "body mass index", "bmi (kg/m2)"], "quantity", "number"
    ),
    CanonicalField(
        "smoking_status",
        "Smoking status",
        ["smoking status", "smoking", "smoker", "tobacco use"],
        "verbatim",
    ),
    CanonicalField(
        "passage",
        "Cell culture passage number",
        ["passage", "passage number", "passages"],
        "quantity",
        "number",
    ),
    CanonicalField(
        "replicate",
        "Biological or technical replicate label",
        ["replicate", "biological replicate", "technical replicate", "rep", "replicate number"],
        "verbatim",
    ),
    CanonicalField(
        "disease_severity",
        "Clinical severity or grade of the disease state (verbatim label)",
        [
            "severity",
            "disease severity",
            "disease_severity",
            "clinical severity",
            "severity group",
            "severity score",
        ],
        "verbatim",
    ),
    CanonicalField(
        "geographic_location",
        "Geographic origin of the sample or donor",
        [
            "geographical location",
            "geographic location",
            "geo_loc_name",
            "geo loc name",
            "country",
            "site location",
            "collection site",
        ],
        "verbatim",
    ),
    CanonicalField(
        "tumor_stage",
        "Tumor stage (TNM / AJCC / clinical / pathological), verbatim label",
        [
            "tnm stage",
            "tumor stage",
            "tumour stage",
            "ajcc stage",
            "pathological stage",
            "clinical stage",
            "stage (tnm)",
            "tnm",
            "cancer stage",
            "pstage",
            "ajcc",
        ],
        "verbatim",
    ),
    CanonicalField(
        "tumor_grade",
        "Histological tumor grade, verbatim label",
        [
            "tumor grade",
            "tumour grade",
            "histological grade",
            "grade (histology)",
            "gleason score",
            "who grade",
        ],
        "verbatim",
    ),
    CanonicalField(
        "overall_survival_time",
        "Overall survival time (parsed with unit when given in the key or value)",
        [
            "os delay",
            "os time",
            "overall survival",
            "overall survival time",
            "os (months)",
            "os months",
            "survival time",
            "survival (months)",
            "os.delay",
        ],
        "quantity",
        "number",
    ),
    CanonicalField(
        "overall_survival_event",
        "Overall survival event indicator (verbatim: 1/0, dead/alive)",
        ["os event", "overall survival event", "os status", "death event", "os.event"],
        "verbatim",
    ),
    CanonicalField(
        "relapse_free_survival_time",
        "Relapse/disease-free survival time (parsed with unit when given)",
        [
            "rfs delay",
            "rfs time",
            "relapse free survival",
            "relapse free survival time",
            "dfs time",
            "dfs delay",
            "disease free survival",
            "pfs time",
            "progression free survival",
        ],
        "quantity",
        "number",
    ),
    CanonicalField(
        "relapse_free_survival_event",
        "Relapse/disease-free survival event indicator (verbatim)",
        [
            "rfs event",
            "relapse event",
            "recurrence event",
            "dfs event",
            "pfs event",
            "relapse free survival event",
            "progression event",
        ],
        "verbatim",
    ),
    CanonicalField(
        "subject_id",
        "Donor, patient or animal identifier",
        [
            "subject id",
            "subject",
            "patient id",
            "patient",
            "donor id",
            "donor",
            "individual",
            "individual id",
            "animal id",
            "mouse id",
            "participant id",
            "case id",
        ],
        "verbatim",
    ),
)

_CONTROL_TOKENS: frozenset[str] = frozenset(
    {
        "control",
        "controls",
        "ctrl",
        "ctl",
        "healthy",
        "healthy control",
        "healthy donor",
        "healthy controls",
        "normal",
        "normal control",
        "non-diseased",
        "none",
        "wild type",
        "wildtype",
        "wt",
        "vehicle",
        "untreated",
        "mock",
        "sham",
        "baseline",
        "healthy volunteer",
        "hc",
        "nc",
    }
)

_SEX_MAP: dict[str, str] = {
    "f": "female",
    "female": "female",
    "woman": "female",
    "women": "female",
    "girl": "female",
    "xx": "female",
    "fem": "female",
    "m": "male",
    "male": "male",
    "man": "male",
    "men": "male",
    "boy": "male",
    "xy": "male",
    "mixed": "mixed",
    "pooled": "mixed",
    "both": "mixed",
    "mixed sex": "mixed",
    "pool": "mixed",
}

_UNIT_TOKENS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("year", ("years", "year", "yrs", "yr", "y", "yo", "y.o.", "years old", "year old")),
    ("month", ("months", "month", "mos", "mo", "mths", "mth")),
    ("week", ("weeks", "week", "wks", "wk", "w")),
    ("day", ("days", "day", "d", "dpi", "dpo")),
    ("hour", ("hours", "hour", "hrs", "hr", "h", "hpi")),
    ("minute", ("minutes", "minute", "min", "mins")),
)
_YEARS_PER_UNIT: dict[str, float] = {
    "year": 1.0,
    "month": 1 / 12,
    "week": 1 / 52.1775,
    "day": 1 / 365.25,
    "hour": 1 / (365.25 * 24),
    "minute": 1 / (365.25 * 24 * 60),
}

_NUM = r"(\d+(?:[.,]\d+)?)"
_RANGE_RE = re.compile(rf"^\s*{_NUM}\s*(?:-|–|to)\s*{_NUM}\s*([a-zA-Z. ]*)$")
_QTY_RE = re.compile(
    rf"^\s*(?:~|≈|approx\.?|about)?\s*{_NUM}\s*([a-zA-Z. ]*?)\s*(?:old)?\s*$", re.I
)
_POSTNATAL_RE = re.compile(r"^\s*p\s*(\d+(?:\.\d+)?)\s*$", re.I)
_EMBRYONIC_RE = re.compile(r"^\s*e\s*(\d+(?:\.\d+)?)\s*$", re.I)
_PAREN_RE = re.compile(r"\s*\([^)]*\)\s*")


class _Registry:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._fields: dict[str, CanonicalField] = {}
        self._synonyms: dict[str, str] = {}
        for f in _DEFAULT_FIELDS:
            self.register_field(
                f.name,
                description=f.description,
                synonyms=f.synonyms,
                normalizer=f.normalizer,
                dtype=f.dtype,
            )

    @staticmethod
    def normalize_key(key: str) -> str:
        k = str(key).strip().lower().rstrip(":")
        k = re.sub(r"[_\-.]+", " ", k)
        k = re.sub(r"\s+", " ", k).strip()
        return k

    def register_field(
        self,
        name: str,
        *,
        description: str,
        synonyms: list[str] | tuple[str, ...] = (),
        normalizer: str = "verbatim",
        dtype: str = "string",
    ) -> CanonicalField:
        cf = self._fields.get(name) or CanonicalField(
            name=name, description=description, normalizer=normalizer, dtype=dtype
        )
        cf.description = description or cf.description
        self._fields[name] = cf
        self.register_synonym(name, name)
        for syn in synonyms:
            self.register_synonym(name, syn)
        return cf

    def register_synonym(self, field_name: str, synonym: str) -> None:
        if field_name not in self._fields:
            raise KeyError(f"Unknown canonical field '{field_name}'")
        norm = self.normalize_key(synonym)
        self._synonyms[norm] = field_name
        cf = self._fields[field_name]
        if synonym not in cf.synonyms:
            cf.synonyms.append(synonym)
        # Note: parentheticals are stripped from *queried* keys (see canonical_field), never from
        # registered synonyms, so 'grade (histology)' does not make the ambiguous 'grade' a synonym.

    def canonical_field(self, key: str) -> str | None:
        norm = self.normalize_key(key)
        if norm in self._synonyms:
            return self._synonyms[norm]
        stripped = re.sub(r"\s+", " ", _PAREN_RE.sub(" ", norm)).strip()
        if stripped != norm and stripped in self._synonyms:
            return self._synonyms[stripped]
        return None

    def fields(self) -> dict[str, CanonicalField]:
        return dict(self._fields)


_registry = _Registry()


def reset() -> None:
    """Restore the default vocabulary (drops runtime registrations)."""
    _registry.reset()


def register_field(
    name: str,
    *,
    description: str,
    synonyms: list[str] | tuple[str, ...] = (),
    normalizer: str = "verbatim",
    dtype: str = "string",
) -> CanonicalField:
    """Register a new canonical field (or extend an existing one)."""
    return _registry.register_field(
        name, description=description, synonyms=synonyms, normalizer=normalizer, dtype=dtype
    )


def register_synonym(field_name: str, synonym: str) -> None:
    """Map an additional characteristic key to a canonical field."""
    _registry.register_synonym(field_name, synonym)


def canonical_field(key: str) -> str | None:
    """Resolve a raw characteristic key to a canonical field name, or None when ambiguous."""
    return _registry.canonical_field(key)


def fields() -> dict[str, CanonicalField]:
    """All canonical fields with descriptions and synonyms (for docs and agents)."""
    return _registry.fields()


_KEY_UNIT_RE = re.compile(r"\(([^)]+)\)")


def unit_from_key(key: str) -> str | None:
    """Unit implied by the key itself, e.g. 'age (years)' -> 'year', 'os.delay (months)' -> 'month'."""
    for m in _KEY_UNIT_RE.finditer(str(key)):
        unit = _unit_from_token(m.group(1).strip().lower())
        if unit:
            return unit
    tail = str(key).strip().lower().rsplit(" ", 1)[-1] if " " in str(key) else ""
    return _unit_from_token(tail) if tail in ("years", "months", "weeks", "days", "hours") else None


def is_missing(raw: Any) -> bool:
    return str(raw).strip().lower() in MISSING_TOKENS if raw is not None else True


# ---------------------------------------------------------------------------
# Value normalizers
# ---------------------------------------------------------------------------


def _to_float(text: str) -> float:
    return float(text.replace(",", "."))


def _unit_from_token(token: str) -> str | None:
    tok = token.strip().lower().rstrip(".")
    for unit, aliases in _UNIT_TOKENS:
        if tok in aliases:
            return unit
    return None


def parse_quantity(raw: str) -> tuple[float | None, str | None, str]:
    """Parse '45 years', '6 mo', 'P56', 'E14.5', '60-69' -> (value, unit, rule)."""
    text = str(raw).strip()
    m = _POSTNATAL_RE.match(text)
    if m:
        return float(m.group(1)), "day", "quantity.postnatal_day"
    m = _EMBRYONIC_RE.match(text)
    if m:
        return float(m.group(1)), "embryonic_day", "quantity.embryonic_day"
    m = _RANGE_RE.match(text)
    if m:
        lo, hi = _to_float(m.group(1)), _to_float(m.group(2))
        unit = _unit_from_token(m.group(3)) if m.group(3).strip() else None
        return (lo + hi) / 2.0, unit, "quantity.range_midpoint"
    m = _QTY_RE.match(text)
    if m:
        value = _to_float(m.group(1))
        unit_token = m.group(2).strip()
        if not unit_token:
            return value, None, "quantity.number"
        unit = _unit_from_token(unit_token)
        if unit is None:
            # e.g. '45 years old' with trailing 'old' handled by regex; unknown trailing words -> unmapped
            cleaned = re.sub(r"\bold\b", "", unit_token).strip()
            unit = _unit_from_token(cleaned) if cleaned else None
            if unit is None:
                return None, None, "quantity.unparsed"
        return value, unit, "quantity.with_unit"
    return None, None, "quantity.unparsed"


def _normalize_sex(raw: str, source_key: str) -> NormalizedValue:
    token = str(raw).strip().lower().rstrip(".")
    mapped = _SEX_MAP.get(token)
    if mapped is None:
        return NormalizedValue(
            value=None,
            raw=str(raw),
            confidence="unmapped",
            rule="sex.unmapped",
            source_key=source_key,
        )
    return NormalizedValue(
        value=mapped, raw=str(raw), confidence="exact", rule="sex.synonym", source_key=source_key
    )


def _normalize_quantity(field_name: str, raw: str, source_key: str) -> NormalizedValue:
    value, unit, rule = parse_quantity(str(raw))
    key_unit = unit_from_key(source_key)
    if value is not None and unit is None and key_unit is not None:
        unit, rule = key_unit, "quantity.unit_from_key"
    extra: dict[str, Any] = {}
    if field_name == "age":
        extra["age_years"] = (
            round(value * _YEARS_PER_UNIT[unit], 6)
            if (value is not None and unit in _YEARS_PER_UNIT)
            else None
        )
    if value is None:
        return NormalizedValue(
            value=None,
            raw=str(raw),
            confidence="unmapped",
            rule=rule,
            source_key=source_key,
            extra=extra,
        )
    confidence = "exact" if unit is not None or rule == "quantity.number" else "inferred"
    if rule in ("quantity.range_midpoint", "quantity.unit_from_key"):
        confidence = "inferred"
    return NormalizedValue(
        value=value,
        raw=str(raw),
        unit=unit,
        confidence=confidence,
        rule=rule,
        source_key=source_key,
        extra=extra,
    )


def _normalize_disease(raw: str, source_key: str) -> NormalizedValue:
    label = str(raw).strip()
    low = label.lower()
    is_control = (
        low in _CONTROL_TOKENS
        or any(tok in low.split() for tok in ("control", "healthy", "ctrl"))
        or low.startswith(("healthy ", "normal "))
    )
    return NormalizedValue(
        value=label,
        raw=str(raw),
        confidence="verbatim",
        rule="disease.verbatim",
        source_key=source_key,
        extra={"is_control": bool(is_control)},
    )


def normalize_value(field_name: str, raw: Any, *, source_key: str | None = None) -> NormalizedValue:
    """Normalize a raw characteristic value for a canonical field."""
    key = source_key if source_key is not None else field_name
    if raw is None or is_missing(raw):
        extra = {"age_years": None} if field_name == "age" else {}
        return NormalizedValue(
            value=None,
            raw="" if raw is None else str(raw),
            confidence="missing",
            rule="missing",
            source_key=key,
            extra=extra,
        )
    cf = _registry.fields().get(field_name)
    normalizer = cf.normalizer if cf else "verbatim"
    text = str(raw)
    if normalizer == "sex":
        return _normalize_sex(text, key)
    if normalizer == "quantity":
        return _normalize_quantity(field_name, text, key)
    if normalizer == "disease":
        return _normalize_disease(text, key)
    return NormalizedValue(
        value=text.strip(), raw=text, confidence="verbatim", rule="verbatim", source_key=key
    )
