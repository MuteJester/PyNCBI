"""Metadata layer core: versioned schema, controlled vocabulary with provenance,
harmonization of ragged characteristics, and quality assessment (all pure, offline)."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from PyNCBI.metadata import (
    SCHEMA_VERSION,
    HarmonizedTable,
    NormalizedValue,
    QualityReport,
    SampleRecord,
    StudyRecord,
    assess_study,
    harmonize_characteristics,
    harmonize_sample,
    json_schema,
    vocabulary,
)

# --- vocabulary ----------------------------------------------------------------------------


def test_canonical_field_resolution_is_conservative():
    assert vocabulary.canonical_field("gender") == "sex"
    assert vocabulary.canonical_field("Sex") == "sex"
    assert vocabulary.canonical_field("sex (male/female)") == "sex"
    assert vocabulary.canonical_field("age") == "age"
    assert vocabulary.canonical_field("age (years)") == "age"
    assert vocabulary.canonical_field("age at diagnosis") == "age"
    assert vocabulary.canonical_field("disease state") == "disease"
    assert vocabulary.canonical_field("disease_status") == "disease"
    assert vocabulary.canonical_field("diagnosis") == "disease"
    assert vocabulary.canonical_field("cell type") == "cell_type"
    assert vocabulary.canonical_field("cell line") == "cell_line"
    assert vocabulary.canonical_field("tissue") == "tissue"
    assert vocabulary.canonical_field("tissue type") == "tissue"
    assert vocabulary.canonical_field("treated with") == "treatment"
    assert vocabulary.canonical_field("strain background") == "strain"
    assert vocabulary.canonical_field("time point") == "time_point"
    assert vocabulary.canonical_field("days_post_symptom_onset") == "time_point"
    assert vocabulary.canonical_field("genotype/variation") == "genotype"
    assert vocabulary.canonical_field("severity") == "disease_severity"
    assert vocabulary.canonical_field("geographical location") == "geographic_location"
    assert vocabulary.canonical_field("geo_loc_name") == "geographic_location"
    # Dotted clinical keys (GSE39582 style) and key-borne units
    assert vocabulary.canonical_field("age.at.diagnosis (year)") == "age"
    assert vocabulary.canonical_field("tnm.stage") == "tumor_stage"
    assert vocabulary.canonical_field("os.delay (months)") == "overall_survival_time"
    assert vocabulary.canonical_field("rfs.event") == "relapse_free_survival_event"
    assert vocabulary.canonical_field("Sex") == "sex"
    # Ambiguous or study-specific keys are NOT mapped
    assert vocabulary.canonical_field("group") is None
    assert vocabulary.canonical_field("grade") is None
    assert vocabulary.canonical_field("stage") is None
    assert vocabulary.canonical_field("time") is None
    assert vocabulary.canonical_field("tumor.location") is None
    assert vocabulary.canonical_field("mmr.status") is None
    assert vocabulary.canonical_field("inhibition (average values)") is None
    assert vocabulary.canonical_field("sample name") is None


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("F", "female"),
        ("female", "female"),
        ("Female", "female"),
        ("M", "male"),
        ("male", "male"),
        ("Man", "male"),
        ("woman", "female"),
        ("mixed", "mixed"),
        ("unknown", None),
        ("NA", None),
        ("", None),
        ("XY", "male"),
        ("XX", "female"),
    ],
)
def test_sex_value_normalization(raw, expected):
    nv = vocabulary.normalize_value("sex", raw)
    assert nv.value == expected
    assert nv.raw == raw
    if expected is None:
        assert nv.confidence in ("unmapped", "missing")
    else:
        assert nv.confidence == "exact"


@pytest.mark.parametrize(
    "raw,value,unit,years",
    [
        ("45", 45.0, None, None),
        ("45 years", 45.0, "year", 45.0),
        ("45 y", 45.0, "year", 45.0),
        ("45yrs", 45.0, "year", 45.0),
        ("6 months", 6.0, "month", 0.5),
        ("12 wk", 12.0, "week", pytest.approx(12 / 52.1775, rel=1e-3)),
        ("P56", 56.0, "day", pytest.approx(56 / 365.25, rel=1e-3)),
        ("E14.5", 14.5, "embryonic_day", None),
        ("2.5 yr", 2.5, "year", 2.5),
        ("NA", None, None, None),
        ("adult", None, None, None),
        ("60-69", 64.5, None, None),
        ("45.5 years old", 45.5, "year", 45.5),
    ],
)
def test_age_parsing_with_units(raw, value, unit, years):
    nv = vocabulary.normalize_value("age", raw)
    assert nv.value == value
    assert nv.unit == unit
    if years is None:
        assert nv.extra.get("age_years") is None
    else:
        assert nv.extra["age_years"] == years
    if value is None:
        assert nv.confidence in ("unmapped", "missing")


def test_units_carried_by_the_key_are_inferred():
    nv = vocabulary.normalize_value("age", "34.5", source_key="age.at.diagnosis (year)")
    assert nv.value == 34.5 and nv.unit == "year" and nv.extra["age_years"] == 34.5
    assert nv.confidence == "inferred" and nv.rule == "quantity.unit_from_key"
    os_t = vocabulary.normalize_value("overall_survival_time", "10", source_key="os.delay (months)")
    assert os_t.value == 10.0 and os_t.unit == "month"
    explicit = vocabulary.normalize_value("age", "45 years", source_key="age (months)")
    assert explicit.unit == "year" and explicit.confidence == "exact"  # value wins over key


def test_disease_value_keeps_label_and_flags_control():
    healthy = vocabulary.normalize_value("disease", "Healthy")
    assert healthy.value == "Healthy" and healthy.extra["is_control"] is True
    ctrl = vocabulary.normalize_value("disease", "control")
    assert ctrl.extra["is_control"] is True
    covid = vocabulary.normalize_value("disease", "COVID-19")
    assert (
        covid.value == "COVID-19"
        and covid.extra["is_control"] is False
        and covid.confidence == "verbatim"
    )


def test_vocabulary_is_extensible_and_documented():
    fields = vocabulary.fields()
    assert {
        "sex",
        "age",
        "tissue",
        "cell_type",
        "disease",
        "treatment",
        "time_point",
        "genotype",
        "strain",
    } <= set(fields)
    assert all(fields[f].description for f in fields)
    vocabulary.register_synonym("sex", "biological sex")
    assert vocabulary.canonical_field("Biological Sex") == "sex"
    vocabulary.register_field(
        "hair_color", description="Hair color", synonyms=["hair colour", "hair color"]
    )
    assert vocabulary.canonical_field("Hair Colour") == "hair_color"
    vocabulary.reset()
    assert vocabulary.canonical_field("hair colour") is None
    assert vocabulary.canonical_field("Biological Sex") == "sex"  # defaults survive reset


# --- harmonization ---------------------------------------------------------------------------


def test_harmonize_characteristics_produces_normalized_table_with_provenance():
    chars = pd.DataFrame(
        {
            "gender": ["F", "M", None],
            "age": ["45 years", "61", "NA"],
            "disease state": ["COVID-19", "Healthy", "COVID-19"],
            "cell type": ["PBMC", "PBMC", "PBMC"],
            "inhibition (average values)": ["1.5", "1.6", "2.5"],
        },
        index=pd.Index(["GSM1", "GSM2", "GSM3"], name="gsm_id"),
    )
    result = harmonize_characteristics(chars)
    assert isinstance(result, HarmonizedTable)
    norm = result.normalized_df
    assert list(norm.index) == ["GSM1", "GSM2", "GSM3"]
    assert (
        norm.loc["GSM1", "sex"] == "female"
        and norm.loc["GSM2", "sex"] == "male"
        and pd.isna(norm.loc["GSM3", "sex"])
    )
    assert (
        norm.loc["GSM1", "age"] == 45.0
        and norm.loc["GSM1", "age_unit"] == "year"
        and norm.loc["GSM1", "age_years"] == 45.0
    )
    assert norm.loc["GSM2", "age"] == 61.0 and pd.isna(norm.loc["GSM2", "age_unit"])
    assert norm.loc["GSM2", "disease"] == "Healthy" and bool(norm.loc["GSM2", "is_control"]) is True
    assert norm.loc["GSM1", "cell_type"] == "PBMC"
    # Unmapped keys are preserved verbatim under their original name, prefixed to avoid collisions
    assert norm.loc["GSM1", "raw:inhibition (average values)"] == "1.5"
    assert result.unmapped_keys == ["inhibition (average values)"]
    assert result.field_map == {
        "gender": "sex",
        "age": "age",
        "disease state": "disease",
        "cell type": "cell_type",
    }
    prov = result.provenance_df
    row = prov[(prov["gsm_id"] == "GSM1") & (prov["field"] == "sex")].iloc[0]
    assert (
        row["source_key"] == "gender"
        and row["raw"] == "F"
        and row["rule"] == "sex.synonym"
        and row["confidence"] == "exact"
    )
    assert 0.0 < result.coverage <= 1.0
    assert result.summary()["mapped_fields"] == ["age", "cell_type", "disease", "sex"]


def test_harmonize_handles_empty_and_conflicting_keys():
    empty = harmonize_characteristics(pd.DataFrame())
    assert empty.normalized_df.empty and empty.coverage == 0.0
    both = pd.DataFrame({"sex": ["F"], "gender": ["male"]}, index=["GSM1"])
    res = harmonize_characteristics(both)
    # First source wins; the conflict is recorded instead of silently overwritten
    assert res.normalized_df.loc["GSM1", "sex"] == "female"
    assert res.conflicts and res.conflicts[0]["field"] == "sex"


def test_harmonize_sample_dict():
    rec = harmonize_sample({"Sex": "M", "age (years)": "70", "tissue": "Whole blood"})
    assert rec["sex"].value == "male" and isinstance(rec["sex"], NormalizedValue)
    assert rec["age"].value == 70.0 and rec["tissue"].value == "Whole blood"


# --- schema ----------------------------------------------------------------------------------


def test_records_serialize_with_schema_version_and_round_trip():
    sample = SampleRecord(
        gsm_id="GSM1",
        title="S1",
        organism="Homo sapiens",
        platform_id="GPL1",
        characteristics={"gender": "F"},
        harmonized={
            "sex": NormalizedValue(
                value="female", raw="F", confidence="exact", rule="sex.synonym", source_key="gender"
            )
        },
        links={"biosample": "SAMN1", "sra_experiment": "SRX1"},
    )
    study = StudyRecord(
        gse_id="GSE1", title="T", organisms=["Homo sapiens"], platforms=["GPL1"], samples=[sample]
    )
    d = study.to_dict()
    assert d["schema_version"] == SCHEMA_VERSION
    assert d["record_type"] == "study" and d["samples"][0]["record_type"] == "sample"
    assert d["samples"][0]["harmonized"]["sex"]["value"] == "female"
    assert d["samples"][0]["harmonized"]["sex"]["provenance"]["rule"] == "sex.synonym"
    json.dumps(d)  # must be plain JSON
    back = StudyRecord.from_dict(d)
    assert back.samples[0].harmonized["sex"].value == "female" and back.gse_id == "GSE1"
    assert back.content_hash() == study.content_hash()
    assert len(study.content_hash()) == 64


def test_json_schema_is_generated_and_matches_published_files():
    from importlib import resources

    for name in ("study", "sample"):
        generated = json_schema(name)
        assert generated["$schema"].startswith("https://json-schema.org/")
        assert generated["title"] == f"PyNCBI {name.title()}Record"
        assert generated["properties"]["schema_version"]["const"] == SCHEMA_VERSION
        published = json.loads(
            resources.files("PyNCBI.resources").joinpath(f"schemas/{name}.schema.json").read_text()
        )
        assert published == generated, f"run scripts/gen_schemas.py: {name}.schema.json is stale"


# --- quality ---------------------------------------------------------------------------------


def test_quality_assessment_scores_and_issues():
    good = StudyRecord(
        gse_id="GSE1",
        title="T",
        summary="S",
        organisms=["Homo sapiens"],
        platforms=["GPL1"],
        experiment_types=["x"],
        samples=[
            SampleRecord(
                gsm_id=f"GSM{i}",
                title=f"s{i}",
                organism="Homo sapiens",
                platform_id="GPL1",
                characteristics={"sex": "F", "age": "40", "disease": "x"},
            )
            for i in range(6)
        ],
        declared_sample_count=6,
        files=[{"name": "a.tar", "size_bytes": 10}],
    )
    report = assess_study(good)
    assert isinstance(report, QualityReport) and report.score >= 85 and report.issues == []

    bad = StudyRecord(
        gse_id="GSE2",
        title="",
        organisms=[],
        platforms=["GPL1", "GPL2"],
        samples=[
            SampleRecord(gsm_id="GSM1", characteristics={"sex": "F"}),
            SampleRecord(gsm_id="GSM2", characteristics={}),
            SampleRecord(gsm_id="GSM3", characteristics={"foo": "1"}),
        ],
        declared_sample_count=5,
        files=[],
    )
    rep = assess_study(bad)
    codes = {i.code for i in rep.issues}
    assert {
        "missing_title",
        "missing_organism",
        "mixed_platforms",
        "sample_count_mismatch",
        "no_files",
        "ragged_characteristics",
    } <= codes
    assert rep.score < 50
    assert all(i.severity in ("error", "warning", "info") for i in rep.issues)
    assert rep.to_dict()["score"] == rep.score
