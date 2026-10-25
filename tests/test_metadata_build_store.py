"""Metadata layer: record builder (GEO + enrichment), durable store with TTL/checksums,
on-disk layout with manifest verification, and context-window aware serving."""

from __future__ import annotations

import json
import time

import pandas as pd
import pytest

from PyNCBI.core.config import Config
from PyNCBI.core.exceptions import DataError
from PyNCBI.metadata import SCHEMA_VERSION, StudyRecord
from PyNCBI.metadata.builder import build_study_record
from PyNCBI.metadata.serving import bounded_payload, paginate, study_markdown
from PyNCBI.metadata.store import MetadataStore, load_study_record, save_study_record

GSE_SOFT = """^SERIES = GSE100
!Series_title = Whole blood methylation in fibromyalgia
!Series_summary = We profiled DNA methylation.
!Series_overall_design = Cases vs controls.
!Series_type = Methylation profiling by array
!Series_pubmed_id = 32433946
!Series_sample_organism = Homo sapiens
!Series_platform_id = GPL13534
!Series_sample_id = GSM1001
!Series_sample_id = GSM1002
!Series_sample_id = GSM1003
!Series_relation = BioProject: https://www.ncbi.nlm.nih.gov/bioproject/PRJNA333
!Series_relation = SRA: https://www.ncbi.nlm.nih.gov/sra?term=SRP123456
!Series_supplementary_file = {base}/geo/series/GSE100/suppl/GSE100_RAW.tar
"""
BATCH_SOFT = """^SAMPLE = GSM1001
!Sample_title = Control_1
!Sample_series_id = GSE100
!Sample_platform_id = GPL13534
!Sample_organism_ch1 = Homo sapiens
!Sample_source_name_ch1 = whole blood
!Sample_characteristics_ch1 = tissue: Whole blood
!Sample_characteristics_ch1 = disease state: Control
!Sample_characteristics_ch1 = gender: F
!Sample_characteristics_ch1 = age: 45 years
!Sample_relation = BioSample: https://www.ncbi.nlm.nih.gov/biosample/SAMN15180526
!Sample_relation = SRA: https://www.ncbi.nlm.nih.gov/sra?term=SRX8500105
!Sample_supplementary_file = {base}/geo/samples/GSM1001/suppl/GSM1001_Grn.idat.gz
!Sample_data_row_count = 10
^SAMPLE = GSM1002
!Sample_title = Case_1
!Sample_series_id = GSE100
!Sample_platform_id = GPL13534
!Sample_organism_ch1 = Homo sapiens
!Sample_characteristics_ch1 = tissue: Whole blood
!Sample_characteristics_ch1 = disease state: Fibromyalgia
!Sample_characteristics_ch1 = gender: M
!Sample_characteristics_ch1 = age: 51
!Sample_data_row_count = 10
^SAMPLE = GSM1003
!Sample_title = Case_2
!Sample_series_id = GSE100
!Sample_platform_id = GPL13534
!Sample_organism_ch1 = Homo sapiens
!Sample_characteristics_ch1 = tissue: Whole blood
!Sample_characteristics_ch1 = disease state: Fibromyalgia
!Sample_characteristics_ch1 = facilitation score: high
!Sample_data_row_count = 10
"""
BIOSAMPLE_DOC = {
    "uid": "15180526",
    "title": "PIT",
    "accession": "SAMN15180526",
    "date": "2021/02/24",
    "taxonomy": "9606",
    "organism": "Homo sapiens",
    "identifiers": "BioSample: SAMN15180526; SRA: SRS6798783; GEO: GSM1001",
    "package": "Generic",
    "sampledata": '<BioSample accession="SAMN15180526"><Ids><Id db="BioSample" is_primary="1">SAMN15180526</Id><Id db="GEO">GSM1001</Id></Ids>'
    '<Attributes><Attribute attribute_name="smoking status" harmonized_name="smoking_status">never</Attribute>'
    '<Attribute attribute_name="age" harmonized_name="age">45</Attribute></Attributes></BioSample>',
}


@pytest.fixture
def fake_study(local_http, monkeypatch):
    base = local_http.base_url
    monkeypatch.setattr(Config, "NCBI_QUERY_URL_TEMPLATE", base + "/query?acc={accession}")
    monkeypatch.setattr(Config, "NCBI_BATCH_GSM_URL_TEMPLATE", base + "/batch?acc={accession}")
    monkeypatch.setattr(Config, "NCBI_EUTILS_BASE_URL", base + "/eutils")
    monkeypatch.setattr(Config, "NCBI_FTP_HTTPS_BASE", base)
    monkeypatch.setattr(Config, "NCBI_GEO_DOWNLOAD_URL", base + "/geo/download/")
    local_http.add("/query?acc=GSE100", GSE_SOFT.format(base=base))
    local_http.add("/batch?acc=GSE100", BATCH_SOFT.format(base=base))
    local_http.add("/geo/series/GSE100/suppl/GSE100_RAW.tar", b"x" * 2048, "application/x-tar")
    local_http.add(
        "/eutils/esummary.fcgi?db=biosample&id=15180526&retmode=json",
        json.dumps({"result": {"uids": ["15180526"], "15180526": BIOSAMPLE_DOC}}),
        "application/json",
    )
    local_http.add(
        "/eutils/esearch.fcgi?db=biosample&term=SAMN15180526&retmax=5&retstart=0&retmode=json",
        json.dumps({"esearchresult": {"count": "1", "idlist": ["15180526"]}}),
        "application/json",
    )
    return local_http


def test_build_study_record_from_geo_with_harmonization_quality_and_links(fake_study):
    rec = build_study_record("GSE100", check_file_sizes=True)
    assert isinstance(rec, StudyRecord)
    assert rec.gse_id == "GSE100" and rec.title.startswith("Whole blood")
    assert rec.declared_sample_count == 3 and rec.sample_count == 3
    assert rec.platforms == ["GPL13534"] and rec.organisms == ["Homo sapiens"]
    assert rec.links["bioproject"] == "PRJNA333" and rec.links["sra_study"] == "SRP123456"
    assert rec.links["pubmed"] == ["32433946"]
    assert (
        rec.files
        and rec.files[0]["name"] == "GSE100_RAW.tar"
        and rec.files[0]["size_bytes"] == 2048
    )
    assert rec.retrieved_at.endswith("Z") and rec.source_versions["schema"] == SCHEMA_VERSION

    s1 = rec.samples[0]
    assert (
        s1.gsm_id == "GSM1001" and s1.organism == "Homo sapiens" and s1.source_name == "whole blood"
    )
    assert s1.characteristics == {
        "tissue": "Whole blood",
        "disease state": "Control",
        "gender": "F",
        "age": "45 years",
    }
    assert s1.links == {
        "biosample": "SAMN15180526",
        "sra_experiment": "SRX8500105",
        "series": "GSE100",
    }
    assert s1.supplementary_files == [
        f"{fake_study.base_url}/geo/samples/GSM1001/suppl/GSM1001_Grn.idat.gz"
    ]
    assert (
        s1.harmonized["sex"].value == "female"
        and s1.harmonized["age"].value == 45.0
        and s1.harmonized["age"].unit == "year"
    )
    assert s1.harmonized["disease"].extra["is_control"] is True
    assert rec.samples[2].harmonized["raw:facilitation score"].value == "high"

    assert rec.harmonization["mapped_fields"] == ["age", "disease", "sex", "tissue"]
    assert rec.harmonization["unmapped_keys"] == ["facilitation score"]
    assert rec.quality is not None and 0 < rec.quality.score <= 100
    codes = {i.code for i in rec.quality.issues}
    assert "ragged_characteristics" in codes  # severity/gender/age not on all samples

    hdf = rec.harmonized_df()
    assert list(hdf.index) == ["GSM1001", "GSM1002", "GSM1003"]
    assert hdf.loc["GSM1002", "sex"] == "male" and pd.isna(hdf.loc["GSM1003", "sex"])
    assert rec.samples_df().loc["GSM1001", "link:biosample"] == "SAMN15180526"
    prov = rec.provenance_df()
    assert set(prov["confidence"]) >= {"exact", "verbatim"}


def test_build_study_record_cohort_filter_and_biosample_enrichment(fake_study):
    rec = build_study_record("GSE100", where="disease state: Control", enrich=("biosample",))
    assert [s.gsm_id for s in rec.samples] == ["GSM1001"]
    s = rec.samples[0]
    assert s.extra["biosample"]["accession"] == "SAMN15180526"
    assert s.extra["biosample"]["attributes"]["smoking_status"] == "never"
    # BioSample attributes are harmonized too, with their own provenance source; GEO wins on conflict-free overlap
    assert (
        s.harmonized["smoking_status"].value == "never"
        and s.harmonized["smoking_status"].source == "biosample:attributes"
    )
    assert s.harmonized["age"].source == "geo:characteristics_ch1"


def test_metadata_store_put_get_ttl_and_index(fake_study, tmp_path):
    store = MetadataStore(tmp_path / "meta", ttl_seconds=3600)
    assert store.get("GSE100") is None
    rec = store.get_or_build("GSE100")
    hits_after_build = len(fake_study.hits)
    again = store.get_or_build("GSE100")
    assert len(fake_study.hits) == hits_after_build  # served from the store
    assert again.content_hash() == rec.content_hash()
    idx = store.index()
    assert (
        idx[0]["gse_id"] == "GSE100"
        and idx[0]["content_hash"] == rec.content_hash()
        and idx[0]["sample_count"] == 3
    )

    stale = MetadataStore(tmp_path / "meta", ttl_seconds=0)
    time.sleep(0.01)
    assert stale.get("GSE100") is None  # expired
    assert stale.get("GSE100", max_age=10**6) is not None  # explicit override
    stale.get_or_build("GSE100")
    assert len(fake_study.hits) > hits_after_build  # rebuilt
    assert store.delete("GSE100") is True and store.get("GSE100") is None


def test_save_and_load_study_record_layout_with_manifest_verification(fake_study, tmp_path):
    rec = build_study_record("GSE100")
    out = tmp_path / "GSE100_meta"
    manifest = save_study_record(rec, out)
    expected = {
        "study.json",
        "samples.csv",
        "samples_harmonized.csv",
        "provenance.csv",
        "quality.json",
        "manifest.json",
        "vocabulary.json",
    }
    assert expected <= {p.name for p in out.iterdir()}
    assert (
        manifest["schema_version"] == SCHEMA_VERSION
        and manifest["content_hash"] == rec.content_hash()
    )
    assert {f["path"] for f in manifest["files"]} == expected - {"manifest.json"}
    assert all(len(f["sha256"]) == 64 and f["bytes"] > 0 for f in manifest["files"])

    loaded = load_study_record(out)
    assert loaded.content_hash() == rec.content_hash()
    assert loaded.samples[0].harmonized["sex"].value == "female"

    harmonized = pd.read_csv(out / "samples_harmonized.csv", index_col=0)
    assert (
        harmonized.loc["GSM1001", "sex"] == "female"
        and harmonized.loc["GSM1001", "age_years"] == 45.0
    )

    (out / "samples.csv").write_text("tampered")
    with pytest.raises(DataError, match="(?i)checksum"):
        load_study_record(out)
    assert load_study_record(out, verify=False).gse_id == "GSE100"


def test_pagination_tokens_and_bounded_payload():
    items = list(range(23))
    page, token, total = paginate(items, page_size=10)
    assert page == list(range(10)) and total == 23 and token
    page2, token2, _ = paginate(items, page_size=10, page_token=token)
    assert page2 == list(range(10, 20))
    page3, token3, _ = paginate(items, page_size=10, page_token=token2)
    assert page3 == [20, 21, 22] and token3 is None
    with pytest.raises(ValueError):
        paginate(items, page_size=10, page_token="not-a-token")
    with pytest.raises(ValueError):
        paginate(
            list(range(5)), page_size=10, page_token=token
        )  # token minted for a different collection

    payload = {"samples": [{"id": i, "text": "x" * 50} for i in range(200)], "title": "t"}
    small = bounded_payload(payload, max_chars=2000)
    assert small["title"] == "t" and len(small["samples"]) < 200
    assert (
        small["_truncated"]["samples"]["shown"] == len(small["samples"])
        and small["_truncated"]["samples"]["total"] == 200
    )
    assert len(json.dumps(small)) <= 2200
    assert bounded_payload(payload, max_chars=10**6) == payload


def test_study_markdown_renders_from_record(fake_study):
    rec = build_study_record("GSE100")
    md = study_markdown(rec, max_samples=2)
    assert md.startswith("# GSE100") and "Quality score" in md and "| sex |" in md or "sex" in md
    assert "GSM1001" in md and "SAMN15180526" in md and "facilitation score" in md
