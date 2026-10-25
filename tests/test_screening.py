"""Cross-study screening: turn many candidate series into one comparison table an agent
can filter on (sample count, available data sources, download size, metadata coverage, quality)."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from PyNCBI.cli.main import main as cli_main
from PyNCBI.core.config import Config
from PyNCBI.domains.geo import rnaseq_counts_url
from PyNCBI.domains.screening import ScreeningResult, screen_studies
from PyNCBI.mcp.server import execute_mcp_tool


def _gse(base: str, gse: str, *, seq: bool, n: int, supp: list[str] = (), pubmed: str = "") -> str:
    typ = (
        "Expression profiling by high throughput sequencing"
        if seq
        else "Methylation profiling by array"
    )
    lines = [
        f"^SERIES = {gse}",
        f"!Series_title = Study {gse}",
        f"!Series_type = {typ}",
        "!Series_sample_organism = Homo sapiens",
        "!Series_platform_id = GPL1",
        f"!Series_summary = Summary of {gse}.",
    ]
    if pubmed:
        lines.append(f"!Series_pubmed_id = {pubmed}")
    lines += [f"!Series_sample_id = GSM{gse[-1]}{i}" for i in range(1, n + 1)]
    lines += [f"!Series_supplementary_file = {base}{p}" for p in supp]
    return "\n".join(lines) + "\n"


def _batch(gse: str, n: int, *, with_sex: bool, with_age: bool) -> str:
    out = []
    for i in range(1, n + 1):
        out += [
            f"^SAMPLE = GSM{gse[-1]}{i}",
            f"!Sample_title = S{i}",
            f"!Sample_series_id = {gse}",
            "!Sample_platform_id = GPL1",
            "!Sample_organism_ch1 = Homo sapiens",
            f"!Sample_characteristics_ch1 = disease state: {'Control' if i % 2 else 'Case'}",
            "!Sample_characteristics_ch1 = tissue: Whole blood",
        ]
        if with_sex:
            out.append(f"!Sample_characteristics_ch1 = gender: {'F' if i % 2 else 'M'}")
        if with_age:
            out.append(f"!Sample_characteristics_ch1 = age: {40 + i} years")
        out.append("!Sample_data_row_count = 0")
    return "\n".join(out) + "\n"


@pytest.fixture
def geo(local_http, monkeypatch):
    base = local_http.base_url
    monkeypatch.setattr(Config, "NCBI_QUERY_URL_TEMPLATE", base + "/query?acc={accession}")
    monkeypatch.setattr(Config, "NCBI_BATCH_GSM_URL_TEMPLATE", base + "/batch?acc={accession}")
    monkeypatch.setattr(Config, "NCBI_EUTILS_BASE_URL", base + "/eutils")
    monkeypatch.setattr(Config, "NCBI_FTP_HTTPS_BASE", base)
    monkeypatch.setattr(Config, "NCBI_GEO_DOWNLOAD_URL", base + "/geo/download/")
    # GSE1: sequencing, NCBI counts available, sex+age
    local_http.add(
        "/query?acc=GSE1",
        _gse(
            base, "GSE1", seq=True, n=4, supp=["/geo/series/GSE1/suppl/GSE1_RAW.tar"], pubmed="111"
        ),
    )
    local_http.add("/batch?acc=GSE1", _batch("GSE1", 4, with_sex=True, with_age=True))
    local_http.add("/geo/series/GSE1/suppl/GSE1_RAW.tar", b"a" * 4096, "application/x-tar")
    local_http.add(rnaseq_counts_url("GSE1")[len(base) :], b"z" * 2048, "application/octet-stream")
    # GSE2: array, series matrix only, sex but no age
    local_http.add("/query?acc=GSE2", _gse(base, "GSE2", seq=False, n=6))
    local_http.add("/batch?acc=GSE2", _batch("GSE2", 6, with_sex=True, with_age=False))
    local_http.add_gzip(
        "/geo/series/GSEnnn/GSE2/matrix/GSE2_series_matrix.txt.gz",
        "!series_matrix_table_begin\nID_REF\tGSM21\np\t1\n!series_matrix_table_end\n",
    )
    # GSE3: array, no sex/age at all
    local_http.add("/query?acc=GSE3", _gse(base, "GSE3", seq=False, n=2))
    local_http.add("/batch?acc=GSE3", _batch("GSE3", 2, with_sex=False, with_age=False))
    return local_http


def test_screen_explicit_accessions_builds_comparison_table(geo):
    res = screen_studies(["GSE1", "GSE2", "GSE3"], max_workers=3)
    assert isinstance(res, ScreeningResult)
    table = res.table
    assert isinstance(table, pd.DataFrame)
    assert list(table.index) == ["GSE1", "GSE2", "GSE3"]
    assert table.index.name == "gse_id"

    row = table.loc["GSE1"]
    assert (
        row["sample_count"] == 4 and row["organism"] == "Homo sapiens" and row["platform"] == "GPL1"
    )
    assert bool(row["has_ncbi_counts"]) is True and bool(row["has_series_matrix"]) is False
    assert row["download_bytes"] == 4096 + 2048 and row["file_count"] == 2
    assert row["download_human"].endswith("KB")
    assert row["harmonized_fields"] == "age, disease, sex, tissue"
    assert row["sex_coverage"] == 1.0 and row["age_coverage"] == 1.0
    assert 0 < row["quality_score"] <= 100
    assert row["pubmed"] == "111"

    assert bool(table.loc["GSE2", "has_series_matrix"]) is True
    assert table.loc["GSE2", "age_coverage"] == 0.0
    assert table.loc["GSE3", "harmonized_fields"] == "disease, tissue"
    assert res.errors == {}
    assert res.to_dict()["study_count"] == 3


def test_screen_requires_fields_and_sorts_by_usefulness(geo):
    res = screen_studies(["GSE1", "GSE2", "GSE3"], require=("sex", "age"))
    assert list(res.table.index) == ["GSE1"]
    assert "sex" in res.notes[0] or "age" in res.notes[0]

    res_sex = screen_studies(["GSE1", "GSE2", "GSE3"], require=("sex",), sort_by="sample_count")
    assert list(res_sex.table.index) == ["GSE2", "GSE1"]


def test_screen_from_a_geo_search_query(geo):
    geo.add(
        "/eutils/esearch.fcgi?db=gds&term=%28whole+blood%29+AND+%22gse%22%5BFilter%5D&retmax=2&retstart=0&retmode=json",
        json.dumps({"esearchresult": {"count": "2", "idlist": ["1", "2"]}}),
        "application/json",
    )
    geo.add(
        "/eutils/esummary.fcgi?db=gds&id=1%2C2&retmode=json",
        json.dumps(
            {
                "result": {
                    "uids": ["1", "2"],
                    "1": {
                        "uid": "1",
                        "accession": "GSE1",
                        "entrytype": "GSE",
                        "title": "Study GSE1",
                        "n_samples": 4,
                        "taxon": "Homo sapiens",
                        "gdstype": "Expression profiling by high throughput sequencing",
                    },
                    "2": {
                        "uid": "2",
                        "accession": "GSE2",
                        "entrytype": "GSE",
                        "title": "Study GSE2",
                        "n_samples": 6,
                        "taxon": "Homo sapiens",
                        "gdstype": "Methylation profiling by array",
                    },
                }
            }
        ),
        "application/json",
    )
    res = screen_studies("whole blood", limit=2)
    assert sorted(res.table.index) == ["GSE1", "GSE2"]
    assert res.query == "whole blood"


def test_screen_records_per_study_failures_without_aborting(geo):
    res = screen_studies(["GSE1", "GSE999999999"])
    assert list(res.table.index) == ["GSE1"]
    assert "GSE999999999" in res.errors
    assert res.errors["GSE999999999"]["error_code"] in ("not_found", "http_error")
    assert any("GSE999999999" in n for n in res.notes)


def test_screen_markdown_and_save(geo, tmp_path):
    res = screen_studies(["GSE1", "GSE2"], save_to=tmp_path / "screen.csv")
    md = res.to_markdown()
    assert md.startswith("# Study screening")
    assert "| GSE1 |" in md and "sex_coverage" in md or "sex" in md
    assert (tmp_path / "screen.csv").exists()
    assert "GSE2" in (tmp_path / "screen.csv").read_text()
    assert res.saved_path is not None


def test_cli_and_mcp_screen(geo, tmp_path, capsys):
    rc = cli_main(["screen", "GSE1", "GSE2", "GSE3", "--require", "sex", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["study_count"] == 2
    assert [r["gse_id"] for r in payload["data"]["rows"]] == ["GSE2", "GSE1"] or [
        r["gse_id"] for r in payload["data"]["rows"]
    ] == ["GSE1", "GSE2"]
    assert "download_human" in payload["data"]["rows"][0]

    out = json.loads(
        execute_mcp_tool("ncbi_screen", {"accessions": ["GSE1", "GSE3"], "require": ["disease"]})
    )
    assert out["data"]["study_count"] == 2
    assert out["data"]["rows"][0]["harmonized_fields"]
