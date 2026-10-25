"""Gene & platform annotation engine: GPL tables, NCBI RNA-seq gene annotation with
GEO -> Datasets fallback, probe/gene-id annotation joins, and CLI surface."""

from __future__ import annotations

import gzip
import json

import pandas as pd
import pytest

from PyNCBI.cli.main import main as cli_main
from PyNCBI.core.config import Config
from PyNCBI.core.exceptions import DataError
from PyNCBI.domains.annotation import (
    annotate_gene_ids,
    annotate_probes,
    detect_gene_columns,
    fetch_gpl_table,
    load_rnaseq_annotation,
)
from PyNCBI.domains.geo import load_rnaseq_counts, rnaseq_counts_url

GPL_TEXT = """^PLATFORM = GPL570
!Platform_title = Test platform
#ID = probe id
!platform_table_begin
ID\tGB_ACC\tGene Symbol\tENTREZ_GENE_ID
1007_s_at\tU48705\tDDR1 /// MIR4640\t780 /// 100616237
1053_at\tM87338\tRFC2\t5982
117_at\tX51757\tHSPA6\t3310
121_at\tX69699\tPAX8\t7849
!platform_table_end
"""

ANNOT_TSV = """GeneID\tSymbol\tDescription\tSynonyms\tGeneType\tEnsemblGeneID\tStatus\tChrAcc\tChrStart\tChrStop\tOrientation\tLength
7157\tTP53\ttumor protein p53\tP53|BCC7\tprotein-coding\tENSG00000141510\tactive\tNC_000017.11\t7668402\t7687538\tminus\t19137
672\tBRCA1\tBRCA1 DNA repair associated\tIRIS|PSCP\tprotein-coding\tENSG00000012048\tactive\tNC_000017.11\t43044295\t43170245\tminus\t125951
100287102\tDDX11L1\tDEAD/H-box helicase 11 like 1\t\ttranscribed_pseudogene\tENSG00000290825\tactive\tNC_000001.11\t11874\t14409\tplus\t2536
"""

RECAPTCHA_HTML = '<!doctype html><html><head><base href="https://www.google.com/recaptcha/challengepage/"></head></html>'


@pytest.fixture
def fake_geo(local_http, monkeypatch):
    base = local_http.base_url
    monkeypatch.setattr(Config, "NCBI_DATA_URL_TEMPLATE", base + "/data?acc={accession}")
    monkeypatch.setattr(Config, "NCBI_QUERY_URL_TEMPLATE", base + "/query?acc={accession}")
    monkeypatch.setattr(Config, "NCBI_GEO_DOWNLOAD_URL", base + "/geo/download/")
    monkeypatch.setattr(Config, "NCBI_DATASETS_BASE_URL", base + "/datasets/v2")
    monkeypatch.setattr(Config, "NCBI_EUTILS_BASE_URL", base + "/eutils")
    local_http.add("/data?acc=GPL570", GPL_TEXT)
    return local_http


# --- GPL tables ----------------------------------------------------------------------------


def test_fetch_gpl_table_streams_full_table_and_caches_it(fake_geo):
    df = fetch_gpl_table("GPL570")
    assert df.index.name == "ID"
    assert df.shape == (4, 3)
    assert df.loc["1053_at", "Gene Symbol"] == "RFC2"

    df2 = fetch_gpl_table("GPL570", columns=["Gene Symbol"])
    assert list(df2.columns) == ["Gene Symbol"]
    # Second call served from the local annotation cache: no new GEO request
    assert sum(1 for h in fake_geo.hits if h.startswith("/data")) == 1


def test_fetch_gpl_table_limit_reads_only_first_rows(fake_geo):
    df = fetch_gpl_table("GPL570", limit=2, use_cache=False)
    assert len(df) == 2
    assert list(df.index) == ["1007_s_at", "1053_at"]


def test_detect_gene_columns_prefers_symbol_then_entrez():
    cols = ["ID", "GB_ACC", "Gene Symbol", "ENTREZ_GENE_ID", "Chromosome"]
    assert detect_gene_columns(cols) == ["Gene Symbol", "ENTREZ_GENE_ID"]
    assert detect_gene_columns(["ID", "UCSC_RefGene_Name", "CHR", "MAPINFO"]) == [
        "UCSC_RefGene_Name"
    ]
    assert detect_gene_columns(["ID", "SPOT_ID"]) == []


def test_annotate_probes_prepends_gene_columns_aligned_to_probe_index(fake_geo):
    expr = pd.DataFrame(
        {"GSM1": [1.0, 2.0, 3.0], "GSM2": [4.0, 5.0, 6.0]},
        index=pd.Index(["1053_at", "121_at", "unknown_at"], name="probe"),
    )
    out = annotate_probes(expr, "GPL570")

    assert list(out.columns) == ["Gene Symbol", "ENTREZ_GENE_ID", "GSM1", "GSM2"]
    assert out.loc["1053_at", "Gene Symbol"] == "RFC2"
    assert out.loc["121_at", "ENTREZ_GENE_ID"] == "7849"
    assert pd.isna(out.loc["unknown_at", "Gene Symbol"])
    # Measurements untouched
    assert out.loc["121_at", "GSM2"] == 5.0


# --- NCBI RNA-seq gene annotation ----------------------------------------------------------


def test_load_rnaseq_annotation_from_geo_file_and_cache(fake_geo):
    fake_geo.add(
        "/geo/download/?type=rnaseq_counts&format=file&file=Human.GRCh38.p13.annot.tsv.gz",
        gzip.compress(ANNOT_TSV.encode()),
        "application/octet-stream",
    )
    table = load_rnaseq_annotation("GRCh38.p13")
    assert table.index.name == "GeneID"
    assert table.loc["7157", "Symbol"] == "TP53"
    assert table.loc["672", "EnsemblGeneID"] == "ENSG00000012048"

    table2 = load_rnaseq_annotation("GRCh38.p13", gene_ids=["672"])
    assert list(table2.index) == ["672"]
    assert sum(1 for h in fake_geo.hits if "annot.tsv.gz" in h) == 1


def test_complete_small_annotation_table_does_not_require_another_download(fake_geo):
    fake_geo.add(
        "/geo/download/?type=rnaseq_counts&format=file&file=Human.GRCh38.p13.annot.tsv.gz",
        gzip.compress(ANNOT_TSV.encode()),
        "application/octet-stream",
    )
    first = load_rnaseq_annotation()
    cached = load_rnaseq_annotation()
    pd.testing.assert_frame_equal(cached, first)
    assert sum("annot.tsv.gz" in hit for hit in fake_geo.hits) == 1


def test_large_partial_annotation_cache_is_not_treated_as_a_complete_genome(monkeypatch):
    from PyNCBI.domains import annotation

    ids = [str(n) for n in range(10001)]
    calls = []
    monkeypatch.setattr(
        annotation, "_fetch_geo_annotation_file", lambda *args: calls.append(args[0])
    )
    monkeypatch.setattr(
        annotation,
        "_fetch_datasets_gene_reports",
        lambda *args: pd.DataFrame(
            {"Symbol": ["x"] * len(ids)}, index=pd.Index(ids, name="GeneID")
        ),
    )
    assert len(load_rnaseq_annotation(gene_ids=ids)) == len(ids)
    with pytest.raises(DataError, match="no gene_ids"):
        load_rnaseq_annotation()
    assert calls == ["GRCh38.p13", "GRCh38.p13"]


def test_load_rnaseq_annotation_falls_back_to_datasets_api_when_geo_is_captcha_gated(fake_geo):
    fake_geo.add(
        "/geo/download/?type=rnaseq_counts&format=file&file=Human.GRCh38.p13.annot.tsv.gz",
        RECAPTCHA_HTML,
        "text/html",
    )
    reports = {
        "reports": [
            {
                "gene": {
                    "gene_id": "7157",
                    "symbol": "TP53",
                    "description": "tumor protein p53",
                    "type": "PROTEIN_CODING",
                    "ensembl_gene_ids": ["ENSG00000141510"],
                    "chromosomes": ["17"],
                    "tax_id": "9606",
                }
            },
            {
                "gene": {
                    "gene_id": "672",
                    "symbol": "BRCA1",
                    "description": "BRCA1 DNA repair associated",
                    "type": "PROTEIN_CODING",
                    "ensembl_gene_ids": ["ENSG00000012048"],
                    "chromosomes": ["17"],
                    "tax_id": "9606",
                }
            },
        ]
    }
    fake_geo.add("/datasets/v2/gene/id/7157,672", json.dumps(reports), "application/json")

    table = load_rnaseq_annotation("GRCh38.p13", gene_ids=["7157", "672"])
    assert table.loc["7157", "Symbol"] == "TP53"
    assert table.loc["672", "GeneType"] == "protein-coding"
    assert table.loc["672", "EnsemblGeneID"] == "ENSG00000012048"
    assert set(table["source"]) == {"ncbi_datasets"}

    # Cached: a second call for the same IDs needs no network at all
    before = len(fake_geo.hits)
    again = load_rnaseq_annotation("GRCh38.p13", gene_ids=["672"])
    assert again.loc["672", "Symbol"] == "BRCA1"
    assert len(fake_geo.hits) == before


def test_load_rnaseq_annotation_without_gene_ids_raises_when_only_fallback_is_available(fake_geo):
    fake_geo.add(
        "/geo/download/?type=rnaseq_counts&format=file&file=Mouse.GRCm39.annot.tsv.gz",
        RECAPTCHA_HTML,
        "text/html",
    )
    with pytest.raises(DataError, match="(?i)gene_ids"):
        load_rnaseq_annotation("GRCm39")


def test_annotate_gene_ids_prepends_symbol_columns_and_keeps_counts(fake_geo):
    fake_geo.add(
        "/geo/download/?type=rnaseq_counts&format=file&file=Human.GRCh38.p13.annot.tsv.gz",
        gzip.compress(ANNOT_TSV.encode()),
        "application/octet-stream",
    )
    counts = pd.DataFrame(
        {"GSM1": [10, 20, 0], "GSM2": [5, 7, 1]},
        index=pd.Index(["7157", "100287102", "999999999"], name="GeneID"),
    )
    out = annotate_gene_ids(counts)
    assert list(out.columns) == ["Symbol", "GeneType", "EnsemblGeneID", "GSM1", "GSM2"]
    assert out.loc["7157", "Symbol"] == "TP53"
    assert out.loc["100287102", "GeneType"] == "transcribed_pseudogene"
    assert pd.isna(out.loc["999999999", "Symbol"])
    assert out.loc["100287102", "GSM1"] == 20

    symbols = annotate_gene_ids(["7157", "672"], columns=("Symbol",))
    assert symbols["Symbol"].tolist() == ["TP53", "BRCA1"]


def test_load_rnaseq_counts_annotate_true_end_to_end(fake_geo):
    fake_geo.add(
        "/query?acc=GSE1",
        "^SERIES = GSE1\n!Series_title = t\n!Series_sample_organism = Homo sapiens\n!Series_type = Expression profiling by high throughput sequencing\n!Series_sample_id = GSM1\n",
    )
    fake_geo.add(
        "/geo/download/?type=rnaseq_counts&format=file&file=Human.GRCh38.p13.annot.tsv.gz",
        gzip.compress(ANNOT_TSV.encode()),
        "application/octet-stream",
    )
    counts_url = rnaseq_counts_url("GSE1", assembly="GRCh38.p13")
    fake_geo.add(
        counts_url[len(fake_geo.base_url) :],
        gzip.compress(b"GeneID\tGSM1\tGSM2\n7157\t3\t4\n672\t1\t0\n"),
        "application/octet-stream",
    )

    df = load_rnaseq_counts("GSE1", annotate=True)
    assert list(df.columns) == ["Symbol", "GeneType", "EnsemblGeneID", "GSM1", "GSM2"]
    assert df.loc["672", "Symbol"] == "BRCA1"
    assert int(df.loc["7157", "GSM2"]) == 4


def test_cli_gpl_command_outputs_table_preview_json(fake_geo, capsys):
    rc = cli_main(
        ["gpl", "GPL570", "--limit", "3", "--columns", "Gene Symbol,ENTREZ_GENE_ID", "--json"]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["data"]["columns"] == ["Gene Symbol", "ENTREZ_GENE_ID"]
    assert payload["data"]["shape"] == [3, 2]


SEQUENCING_PLATFORM_SOFT = """^PLATFORM = GPL24676
!Platform_title = Illumina NovaSeq 6000 (Homo sapiens)
!Platform_technology = high-throughput sequencing
!Platform_distribution = virtual
!Platform_organism = Homo sapiens
!Platform_data_row_count = 0
"""


def test_sequencing_platform_reports_why_it_has_no_probe_table(local_http, monkeypatch):
    """A virtual sequencing platform genuinely has no probe table.

    GEO says so in the record itself (`technology = high-throughput sequencing`,
    `data_row_count = 0`), so the error must say that and point at the right tool,
    instead of looking like a parse failure.
    """
    from PyNCBI import fetch_gpl_table
    from PyNCBI.core.config import Config
    from PyNCBI.core.exceptions import NoDataAvailableError

    base = local_http.base_url
    # GEO's `view=data` reply for a virtual platform really is just this one line.
    monkeypatch.setattr(Config, "NCBI_DATA_URL_TEMPLATE", base + "/gpl-data?acc={accession}")
    monkeypatch.setattr(Config, "NCBI_QUERY_URL_TEMPLATE", base + "/gpl-self?acc={accession}")
    local_http.add("/gpl-data?acc=GPL24676", "^PLATFORM = GPL24676\n")
    local_http.add("/gpl-self?acc=GPL24676", SEQUENCING_PLATFORM_SOFT)

    with pytest.raises(NoDataAvailableError) as excinfo:
        fetch_gpl_table("GPL24676")

    message = f"{excinfo.value} {excinfo.value.hint or ''}".lower()
    assert "sequencing" in message
    assert "annotate_gene_ids" in message or "load_rnaseq_annotation" in message
