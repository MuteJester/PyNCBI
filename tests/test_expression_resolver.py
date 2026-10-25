"""`load_expression`: one call that resolves the best analysis-ready matrix for a GSE
(NCBI counts -> Series Matrix -> tabular supplementary -> per-sample tables -> tar members),
aligns columns to GSM IDs, and reports which source was used and why."""

from __future__ import annotations

import gzip
import io
import json
import tarfile

import pytest

from native_support import cell, data_fields, data_shape, fixture_table, identifiers, table_empty
from PyNCBI.cli.main import main as cli_main
from PyNCBI.core.config import Config
from PyNCBI.core.exceptions import DataError, NoDataAvailableError
from PyNCBI.domains.expression import ExpressionResult, load_expression, map_columns_to_gsms
from PyNCBI.domains.geo import rnaseq_counts_url
from PyNCBI.mcp.server import execute_mcp_tool


def _gse(base: str, gse: str, *, seq: bool, supp: list[str], n: int = 3) -> str:
    typ = (
        "Expression profiling by high throughput sequencing"
        if seq
        else "Expression profiling by array"
    )
    lines = [
        f"^SERIES = {gse}",
        f"!Series_title = Study {gse}",
        f"!Series_type = {typ}",
        "!Series_sample_organism = Homo sapiens",
        "!Series_platform_id = GPL1",
    ]
    lines += [f"!Series_sample_id = GSM{i}" for i in range(1, n + 1)]
    lines += [f"!Series_supplementary_file = {base}{path}" for path in supp]
    return "\n".join(lines) + "\n"


def _batch(
    base: str, gse: str, n: int = 3, *, per_sample_files: bool = False, table_rows: int = 0
) -> str:
    out = []
    for i in range(1, n + 1):
        out += [
            f"^SAMPLE = GSM{i}",
            f"!Sample_title = Patient_{i}_rep1",
            f"!Sample_series_id = {gse}",
            "!Sample_platform_id = GPL1",
            f"!Sample_characteristics_ch1 = group: {'case' if i % 2 else 'control'}",
        ]
        if per_sample_files:
            out.append(
                f"!Sample_supplementary_file = {base}/geo/samples/GSM{i}/suppl/GSM{i}_counts.txt.gz"
            )
        out.append(f"!Sample_data_row_count = {table_rows}")
    return "\n".join(out) + "\n"


@pytest.fixture
def geo(local_http, monkeypatch):
    base = local_http.base_url
    monkeypatch.setattr(Config, "NCBI_QUERY_URL_TEMPLATE", base + "/query?acc={accession}")
    monkeypatch.setattr(Config, "NCBI_BATCH_GSM_URL_TEMPLATE", base + "/batch?acc={accession}")
    monkeypatch.setattr(Config, "NCBI_DATA_URL_TEMPLATE", base + "/data?acc={accession}")
    monkeypatch.setattr(Config, "NCBI_FTP_HTTPS_BASE", base)
    monkeypatch.setattr(Config, "NCBI_GEO_DOWNLOAD_URL", base + "/geo/download/")
    return local_http


def test_source_1_ncbi_counts_preferred_for_sequencing(geo):
    base = geo.base_url
    geo.add(
        "/query?acc=GSE1",
        _gse(base, "GSE1", seq=True, supp=["/geo/series/GSE1/suppl/GSE1_RAW.tar"]),
    )
    geo.add("/batch?acc=GSE1", _batch(base, "GSE1"))
    geo.add("/geo/series/GSE1/suppl/GSE1_RAW.tar", b"x" * 100, "application/x-tar")
    geo.add(
        rnaseq_counts_url("GSE1")[len(base) :],
        gzip.compress(b"GeneID\tGSM1\tGSM2\tGSM3\n7157\t3\t4\t5\n672\t1\t0\t2\n"),
        "application/octet-stream",
    )
    geo.add(
        rnaseq_counts_url("GSE1", count_type="norm_counts_TPM")[len(base) :],
        gzip.compress(b"GeneID\tGSM1\n7157\t1.0\n"),
        "application/octet-stream",
    )

    res = load_expression("GSE1")
    assert isinstance(res, ExpressionResult)
    assert res.source == "ncbi_rnaseq_counts" and data_shape(res.matrix) == (2, 3)
    assert (
        list(data_fields(res.matrix)) == ["GSM1", "GSM2", "GSM3"]
        and res.matrix["columns"][0] == "feature_id"
    )
    assert cell(res.sample_table, "GSM1", "group") == "case" and list(
        identifiers(res.sample_table)
    ) == [
        "GSM1",
        "GSM2",
        "GSM3",
    ]
    assert res.column_mapping == {"GSM1": "GSM1", "GSM2": "GSM2", "GSM3": "GSM3"}
    assert [c["source"] for c in res.candidates][0] == "ncbi_rnaseq_counts"
    assert res.to_dict()["shape"] == [2, 3]


def test_source_2_series_matrix_for_arrays(geo):
    base = geo.base_url
    geo.add("/query?acc=GSE2", _gse(base, "GSE2", seq=False, supp=[]))
    geo.add("/batch?acc=GSE2", _batch(base, "GSE2"))
    geo.add_gzip(
        "/geo/series/GSEnnn/GSE2/matrix/GSE2_series_matrix.txt.gz",
        '!Series_geo_accession\t"GSE2"\n!Sample_geo_accession\t"GSM1"\t"GSM2"\t"GSM3"\n!series_matrix_table_begin\n"ID_REF"\t"GSM1"\t"GSM2"\t"GSM3"\n"p1"\t1.0\t2.0\t3.0\n"p2"\t4.0\t5.0\t6.0\n!series_matrix_table_end\n',
    )
    res = load_expression("GSE2")
    assert (
        res.source == "series_matrix"
        and data_shape(res.matrix) == (2, 3)
        and cell(res.matrix, "p2", "GSM3") == 6.0
    )


def test_source_3_supplementary_table_with_title_columns_is_mapped_to_gsms(geo):
    base = geo.base_url
    geo.add(
        "/query?acc=GSE3",
        _gse(
            base,
            "GSE3",
            seq=True,
            supp=[
                "/geo/series/GSE3/suppl/GSE3_RAW.tar",
                "/geo/series/GSE3/suppl/GSE3_normalized_counts.csv.gz",
            ],
        ),
    )
    geo.add("/batch?acc=GSE3", _batch(base, "GSE3"))
    # empty series matrix (sequencing study) and no NCBI counts
    geo.add_gzip(
        "/geo/series/GSEnnn/GSE3/matrix/GSE3_series_matrix.txt.gz",
        '!Series_geo_accession\t"GSE3"\n!series_matrix_table_begin\n"ID_REF"\t"GSM1"\t"GSM2"\t"GSM3"\n!series_matrix_table_end\n',
    )
    geo.add("/geo/series/GSE3/suppl/GSE3_RAW.tar", b"x" * 100, "application/x-tar")
    geo.add(
        "/geo/series/GSE3/suppl/GSE3_normalized_counts.csv.gz",
        gzip.compress(
            b"gene,Patient_1_rep1,Patient_2_rep1,Patient_3_rep1\nTP53,1,2,3\nBRCA1,4,5,6\n"
        ),
        "application/octet-stream",
    )

    res = load_expression("GSE3")
    assert (
        res.source == "supplementary_table" and res.source_file == "GSE3_normalized_counts.csv.gz"
    )
    assert list(data_fields(res.matrix)) == ["GSM1", "GSM2", "GSM3"]
    assert res.column_mapping["Patient_2_rep1"] == "GSM2"
    assert cell(res.matrix, "BRCA1", "GSM3") == 6
    assert any("mapped by sample title" in n.lower() for n in res.notes)


def test_source_4_per_sample_supplementary_tables_are_joined(geo):
    base = geo.base_url
    geo.add(
        "/query?acc=GSE4",
        _gse(base, "GSE4", seq=True, supp=["/geo/series/GSE4/suppl/GSE4_RAW.tar"]),
    )
    geo.add("/batch?acc=GSE4", _batch(base, "GSE4", per_sample_files=True))
    geo.add_gzip(
        "/geo/series/GSEnnn/GSE4/matrix/GSE4_series_matrix.txt.gz",
        '!series_matrix_table_begin\n"ID_REF"\n!series_matrix_table_end\n',
    )
    geo.add("/geo/series/GSE4/suppl/GSE4_RAW.tar", b"x" * 100, "application/x-tar")
    for i in (1, 2, 3):
        geo.add(
            f"/geo/samples/GSM{i}/suppl/GSM{i}_counts.txt.gz",
            gzip.compress(f"gene\tcount\nTP53\t{i}\nBRCA1\t{10 * i}\n".encode()),
            "application/octet-stream",
        )

    res = load_expression("GSE4", max_workers=2)
    assert res.source == "per_sample_supplementary" and list(data_fields(res.matrix)) == [
        "GSM1",
        "GSM2",
        "GSM3",
    ]
    assert cell(res.matrix, "BRCA1", "GSM2") == 20


def test_source_5_on_page_tables_and_source_6_tar_members(geo):
    base = geo.base_url
    # on-page tables
    geo.add("/query?acc=GSE5", _gse(base, "GSE5", seq=False, supp=[]))
    geo.add("/batch?acc=GSE5", _batch(base, "GSE5", table_rows=2))
    geo.add_gzip(
        "/geo/series/GSEnnn/GSE5/matrix/GSE5_series_matrix.txt.gz",
        '!series_matrix_table_begin\n"ID_REF"\n!series_matrix_table_end\n',
    )
    for i in (1, 2, 3):
        geo.add(
            f"/data?acc=GSM{i}",
            f"^SAMPLE = GSM{i}\n!sample_table_begin\nID_REF\tVALUE\ncg1\t{i}.5\ncg2\t{i}.0\n!sample_table_end\n",
        )
    res = load_expression("GSE5")
    assert res.source == "on_page_tables" and cell(res.matrix, "cg1", "GSM2") == pytest.approx(2.5)

    # tar members
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for i in (1, 2, 3):
            data = gzip.compress(f"id\tvalue\nA\t{i}\nB\t{i + 10}\n".encode())
            info = tarfile.TarInfo(f"GSM{i}_expr.txt.gz")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    geo.add(
        "/query?acc=GSE6",
        _gse(base, "GSE6", seq=False, supp=["/geo/series/GSE6/suppl/GSE6_RAW.tar"]),
    )
    geo.add("/batch?acc=GSE6", _batch(base, "GSE6"))
    geo.add_gzip(
        "/geo/series/GSEnnn/GSE6/matrix/GSE6_series_matrix.txt.gz",
        '!series_matrix_table_begin\n"ID_REF"\n!series_matrix_table_end\n',
    )
    geo.add("/geo/series/GSE6/suppl/GSE6_RAW.tar", buf.getvalue(), "application/x-tar")
    res6 = load_expression("GSE6")
    assert res6.source == "tar_members" and cell(res6.matrix, "B", "GSM3") == 13


def test_prefer_size_guard_and_no_source(geo, tmp_path):
    base = geo.base_url
    geo.add(
        "/query?acc=GSE7",
        _gse(base, "GSE7", seq=False, supp=["/geo/series/GSE7/suppl/GSE7_big_matrix.txt.gz"]),
    )
    geo.add("/batch?acc=GSE7", _batch(base, "GSE7"))
    geo.add_gzip(
        "/geo/series/GSEnnn/GSE7/matrix/GSE7_series_matrix.txt.gz",
        '!series_matrix_table_begin\n"ID_REF"\n!series_matrix_table_end\n',
    )
    geo.add(
        "/geo/series/GSE7/suppl/GSE7_big_matrix.txt.gz",
        gzip.compress(b"gene\tGSM1\tGSM2\tGSM3\nX\t1\t2\t3\n"),
        "application/octet-stream",
    )
    with pytest.raises(DataError, match="(?i)max_download"):
        load_expression("GSE7", max_download_mb=0.00001)
    res = load_expression("GSE7", prefer="supp")
    assert res.source == "supplementary_table"
    out = load_expression("GSE7", save_to=tmp_path / "m.csv")
    assert (tmp_path / "m.csv").exists() and out.saved_path is not None

    geo.add("/query?acc=GSE8", _gse(base, "GSE8", seq=False, supp=[]))
    geo.add("/batch?acc=GSE8", _batch(base, "GSE8"))
    geo.add_gzip(
        "/geo/series/GSEnnn/GSE8/matrix/GSE8_series_matrix.txt.gz",
        '!series_matrix_table_begin\n"ID_REF"\n!series_matrix_table_end\n',
    )
    with pytest.raises(NoDataAvailableError) as excinfo:
        load_expression("GSE8")
    assert (
        "candidates" in str(excinfo.value).lower()
        or "no analysis-ready" in str(excinfo.value).lower()
    )


def test_map_columns_to_gsms_heuristics():
    samples = fixture_table(
        {"title": ["Liver_A", "Liver_B", "Kidney_A"], "description": ["S1", "S2", "S3"]},
        index=["GSM1", "GSM2", "GSM3"],
    )
    mapping, notes = map_columns_to_gsms(["GSM2", "Liver_A", "S3", "unknown"], samples)
    assert mapping == {"GSM2": "GSM2", "Liver_A": "GSM1", "S3": "GSM3"}
    assert any("unknown" in n for n in notes)
    # ambiguous titles are not mapped
    dup = fixture_table({"title": ["rep", "rep"]}, index=["GSM1", "GSM2"])
    mapping2, _ = map_columns_to_gsms(["rep"], dup)
    assert mapping2 == {}


def test_columns_kept_when_series_has_no_sample_sheet(geo):
    """GSE122505-style series: no !Series_sample_id, but a supplementary matrix with GSM columns."""
    base = geo.base_url
    geo.add(
        "/query?acc=GSE10",
        _gse(base, "GSE10", seq=False, supp=["/geo/series/GSE10/suppl/GSE10_matrix.txt.gz"], n=0),
    )
    geo.add("/batch?acc=GSE10", "")
    geo.add_gzip(
        "/geo/series/GSEnnn/GSE10/matrix/GSE10_series_matrix.txt.gz",
        '!series_matrix_table_begin\n"ID_REF"\n!series_matrix_table_end\n',
    )
    geo.add(
        "/geo/series/GSE10/suppl/GSE10_matrix.txt.gz",
        gzip.compress(b"ID_REF\tGSM1\tGSM2\nprobe1\t1\t2\n"),
        "application/octet-stream",
    )

    res = load_expression("GSE10")
    assert res.source == "supplementary_table"
    assert list(data_fields(res.matrix)) == ["GSM1", "GSM2"]  # kept as published
    assert res.column_mapping == {}
    assert table_empty(res.sample_table)
    assert any("kept as published" in n.lower() for n in res.notes)
    assert not any("ignored" in n.lower() for n in res.notes)


def test_map_columns_to_gsms_without_a_sample_sheet():
    mapping, notes = map_columns_to_gsms(["GSM1", "x"], fixture_table())
    assert mapping == {}
    assert any("sample sheet unavailable" in n.lower() for n in notes)


def test_cli_and_mcp_expression(geo, tmp_path, capsys):
    base = geo.base_url
    geo.add("/query?acc=GSE9", _gse(base, "GSE9", seq=False, supp=[]))
    geo.add("/batch?acc=GSE9", _batch(base, "GSE9"))
    geo.add_gzip(
        "/geo/series/GSEnnn/GSE9/matrix/GSE9_series_matrix.txt.gz",
        '!Sample_geo_accession\t"GSM1"\t"GSM2"\t"GSM3"\n!series_matrix_table_begin\n"ID_REF"\t"GSM1"\t"GSM2"\t"GSM3"\n"p1"\t1\t2\t3\n!series_matrix_table_end\n',
    )
    rc = cli_main(["expression", "GSE9", "-o", str(tmp_path / "GSE9.csv"), "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["source"] == "series_matrix" and payload["data"]["shape"] == [1, 3]
    assert (tmp_path / "GSE9.csv").exists() and (tmp_path / "GSE9_samples.csv").exists()
    out = json.loads(
        execute_mcp_tool(
            "ncbi_expression", {"gse_id": "GSE9", "output_path": str(tmp_path / "m2.csv")}
        )
    )
    assert out["data"]["source"] == "series_matrix" and out["saved_files"]
