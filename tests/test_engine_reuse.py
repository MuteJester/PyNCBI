"""Behavior stays consistent across the APIs that share retrieval infrastructure."""

from __future__ import annotations

import bz2
import gzip
import json

import pytest

from native_support import assert_equal, cell, cell_at, identifiers, is_missing, table_empty, values
from PyNCBI.core.config import Config, config_scope
from PyNCBI.core.exceptions import DataError
from PyNCBI.core.serialization import json_dumps
from PyNCBI.core.types import PatternFileSelector, SmartFileSelector, is_tabular_file
from PyNCBI.domains.expression import load_expression
from PyNCBI.domains.geo import (
    download_geo_files,
    plan_geo_download,
    preview_geo_file,
    read_supplementary_table,
)
from PyNCBI.io.http_client import NCBIClient
from PyNCBI.io.urls import download_filename
from PyNCBI.metadata.schema import StudyRecord
from PyNCBI.metadata.store import load_study_record, save_study_record
from PyNCBI.parsing.gds_parser import parse_gds_soft
from PyNCBI.parsing.matrix_parser import SeriesMatrixParser
from PyNCBI.parsing.soft_parser import SOFTParser


@pytest.fixture
def query_file(local_http, monkeypatch):
    monkeypatch.setattr(
        Config, "NCBI_QUERY_URL_TEMPLATE", local_http.base_url + "/query?acc={accession}"
    )
    monkeypatch.setattr(
        Config, "NCBI_BATCH_GSM_URL_TEMPLATE", local_http.base_url + "/batch?acc={accession}"
    )
    url = local_http.add(
        "/download?file=counts+matrix.csv.gz&token=ignored",
        gzip.compress(b"probe,GSM1\n0001,0.12345678901234567\nNA,0.5\n"),
        "application/octet-stream",
    )
    local_http.add(
        "/query?acc=GSE1",
        f"""^SERIES = GSE1
!Series_title = Query file
!Series_sample_id = GSM1
!Series_supplementary_file = {url}
""",
    )
    local_http.add(
        "/batch?acc=GSE1",
        """^SAMPLE = GSM1
!Sample_title = Patient1
!Sample_series_id = GSE1
!Sample_characteristics_ch1 = group: case
""",
    )
    return url


def test_query_filename_selection_agrees_across_public_apis(query_file, tmp_path):
    assert download_filename(query_file + "#section") == "counts matrix.csv.gz"
    assert is_tabular_file(query_file)
    assert PatternFileSelector("*.csv.gz").select(["raw.tar", query_file], "GSE1") == 1
    assert SmartFileSelector().select(["raw.tar", query_file], "GSE1") == 1
    plan = plan_geo_download("GSE1", source="supp", pattern="counts matrix*")
    preview = preview_geo_file("GSE1", source="supp", pattern="counts matrix*")
    saved = download_geo_files("GSE1", tmp_path, source="supp", pattern="counts matrix*")
    assert (
        [f["name"] for f in plan.files]
        == [preview.file_name]
        == [saved[0].name]
        == ["counts matrix.csv.gz"]
    )


def test_expression_and_direct_table_loading_preserve_the_same_cells(query_file):
    direct = read_supplementary_table(query_file)
    resolved = load_expression("GSE1", prefer="supp").matrix
    assert_equal(resolved, direct)
    assert identifiers(direct) == ["0001", "NA"]
    assert cell_at(direct, 0, 0) == float("0.12345678901234567")


@pytest.mark.parametrize("route", ["download", "expression"])
def test_preflight_and_transfer_use_the_explicit_clients_budget(query_file, tmp_path, route):
    with (
        NCBIClient(app_config=Config(max_download_mb=1)) as client,
        config_scope(Config(max_download_mb=0.000001)),
    ):
        if route == "download":
            assert download_geo_files("GSE1", tmp_path, source="supp", http_client=client)
        else:
            assert not table_empty(
                load_expression("GSE1", prefer="supp", http_client=client).matrix
            )


@pytest.mark.parametrize("body", [b"<html>missing file</html>", b"\x1f\x8btruncated"])
def test_direct_table_loader_rejects_invalid_payloads_with_domain_errors(local_http, body):
    url = local_http.add("/suppl/table.tsv.gz", body, "application/octet-stream")
    with pytest.raises(DataError):
        read_supplementary_table(url)


def test_gds_uses_exact_measurement_parsing_and_preserves_annotation_ids():
    result = parse_gds_soft(
        """^DATASET = GDS1
!dataset_table_begin
ID_REF\tIDENTIFIER\tGSM1\tGSM2
0001\t0007\t0.12345678901234567\t9007199254740993
NA\tNA\tnull\t9007199254740995
!dataset_table_end
""".splitlines()
    )
    assert identifiers(result.table) == ["0001", "NA"]
    assert values(result.table, "IDENTIFIER") == ["0007", "NA"]
    assert cell(result.table, "0001", "GSM1") == float("0.12345678901234567")
    assert is_missing(cell(result.table, "NA", "GSM1"))
    assert values(result.table, "GSM2") == [9007199254740993, 9007199254740995]


def test_gds_does_not_silently_discard_malformed_measurements():
    with pytest.raises(DataError, match="Non-numeric measurements.*GSM1"):
        parse_gds_soft(
            "!dataset_table_begin\nID_REF\tGSM1\nprobe1\tinvalid\n!dataset_table_end".splitlines(),
            "GDS1",
        )


@pytest.mark.parametrize("kind", ["matrix", "gsm", "local_csv", "local_gzip", "local_bz2"])
def test_table_entrypoints_preserve_literal_na_feature_ids(kind, tmp_path):
    table = "ID_REF\tGSM1\nNA\t0.12345678901234567\n0001\t1\n"
    if kind == "matrix":
        frame = (
            SeriesMatrixParser()
            .parse("!series_matrix_table_begin\n" + table + "!series_matrix_table_end")
            .matrix
        )
    elif kind == "gsm":
        frame = SOFTParser().parse_gsm_data_table(
            "!sample_table_begin\n" + table.replace("GSM1", "VALUE") + "!sample_table_end", "GSM1"
        )
    else:
        raw = table.replace("\t", ",").encode()
        name = "counts.csv"
        if kind == "local_gzip":
            raw, name = gzip.compress(raw), name + ".gz"
        elif kind == "local_bz2":
            raw, name = bz2.compress(raw), name + ".bz2"
        path = tmp_path / name
        path.write_bytes(raw)
        frame = read_supplementary_table(path)
    assert identifiers(frame) == ["NA", "0001"]
    assert cell(frame, "NA", "GSM1") == float("0.12345678901234567")


def test_study_exports_and_content_hash_share_strict_json_semantics(tmp_path):
    record = StudyRecord(
        gse_id="GSE1",
        extra={
            "missing": float("nan"),
            "count": 9007199254740993,
            "value": 0.12345678901234567,
        },
    )
    manifest = save_study_record(record, tmp_path)
    exported = json.loads(
        (tmp_path / "study.json").read_text(encoding="utf-8"),
        parse_constant=lambda token: pytest.fail(f"nonstandard JSON: {token}"),
    )
    assert exported["extra"] == json.loads(json_dumps(record.extra))
    assert exported["extra"]["count"] == 9007199254740993
    assert exported["extra"]["missing"] is None
    assert (
        load_study_record(tmp_path).content_hash()
        == manifest["content_hash"]
        == record.content_hash()
    )
