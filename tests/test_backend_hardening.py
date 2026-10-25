"""Backend hardening tests: streaming client, bot-challenge detection, GPL table parsing,
RNA-seq assembly resolution, and parallel multi-file downloads."""

from __future__ import annotations

import gzip

import pytest

from PyNCBI.core.exceptions import BotChallengeError, DataError
from PyNCBI.core.types import GSEInfo
from PyNCBI.io.http_client import NCBIClient
from PyNCBI.parsing.soft_parser import get_parser

RECAPTCHA_HTML = (
    '<!doctype html><html lang="en-US"><head><base href="https://www.google.com/recaptcha/'
    'challengepage/"><script>window["ppConfig"] = {productName: "RecaptchaChallengePageUi"}'
    "</script></head><body></body></html>"
)

GPL_DATA_TEXT = """^PLATFORM = GPL570
!Platform_title = [HG-U133_Plus_2] Affymetrix Human Genome U133 Plus 2.0 Array
#ID = Affymetrix Probe Set ID
#Gene Symbol = A gene symbol, when one is available
#ENTREZ_GENE_ID = Entrez Gene Database UID
!platform_table_begin
ID\tGB_ACC\tGene Symbol\tENTREZ_GENE_ID\tChromosome
1007_s_at\tU48705\tDDR1 /// MIR4640\t780 /// 100616237\t6
1053_at\tM87338\tRFC2\t5982\t7
117_at\tX51757\tHSPA6\t3310\t1
121_at\tX69699\tPAX8\t7849\t2
!platform_table_end
"""


# --- 1. NCBIClient.stream_lines ----------------------------------------------------------


def test_stream_lines_yields_decoded_lines_from_plain_and_gzip(local_http):
    text = "\n".join(f"line{i}" for i in range(2000)) + "\n"
    plain_url = local_http.add("/plain.txt", text)
    gz_url = local_http.add_gzip("/table.tsv.gz", text)
    client = NCBIClient()

    plain_lines = list(client.stream_lines(plain_url))
    gz_lines = list(client.stream_lines(gz_url))

    assert plain_lines[0] == "line0"
    assert plain_lines[-1] == "line1999"
    assert len(plain_lines) == 2000
    assert gz_lines == plain_lines


def test_stream_lines_stops_early_without_reading_whole_body(local_http):
    text = "\n".join(f"row{i}" for i in range(200_000)) + "\n"
    url = local_http.add("/big.txt", text)
    client = NCBIClient()

    gen = client.stream_lines(url)
    first_five = [next(gen) for _ in range(5)]
    gen.close()

    assert first_five == ["row0", "row1", "row2", "row3", "row4"]
    last = client.metrics.summary(include_recent=1)["recent_requests"][-1]
    assert last["bytes_received"] < len(text) // 4


# --- 2. Bot-challenge (reCAPTCHA) detection ----------------------------------------------


def test_recaptcha_challenge_page_raises_bot_challenge_error_on_geo_realm():
    client = NCBIClient()
    with pytest.raises(BotChallengeError) as excinfo:
        client._inspect_application_errors(
            "https://www.ncbi.nlm.nih.gov/geo/download/?type=rnaseq_counts&file=x.gz",
            "geo",
            200,
            RECAPTCHA_HTML,
        )
    assert "challenge" in str(excinfo.value).lower()
    assert excinfo.value.hint


def test_get_text_on_challenge_page_raises_bot_challenge_error(local_http):
    # The local server is realm 'external'; detection must not depend on hostname alone
    url = local_http.add("/geo/download/?type=rnaseq_counts&file=x.gz", RECAPTCHA_HTML, "text/html")
    client = NCBIClient()
    with pytest.raises(BotChallengeError):
        client.get_text(url)


# --- 3. GPL multi-column data table parsing ------------------------------------------------


def test_parse_gpl_data_table_preserves_all_columns_and_id_index():
    parser = get_parser()
    df = parser.parse_gpl_data_table(GPL_DATA_TEXT)

    assert df.index.name == "ID"
    assert list(df.index) == ["1007_s_at", "1053_at", "117_at", "121_at"]
    assert list(df.columns) == ["GB_ACC", "Gene Symbol", "ENTREZ_GENE_ID", "Chromosome"]
    # Values are kept as raw strings (no float coercion of multi-valued annotation cells)
    assert df.loc["1007_s_at", "ENTREZ_GENE_ID"] == "780 /// 100616237"
    assert df.loc["1053_at", "Gene Symbol"] == "RFC2"


def test_parse_gpl_data_table_from_streamed_lines_with_column_subset_and_limit():
    parser = get_parser()
    lines = iter(GPL_DATA_TEXT.splitlines())
    df = parser.parse_gpl_table_lines(lines, columns=["Gene Symbol", "ENTREZ_GENE_ID"], limit=2)

    assert list(df.columns) == ["Gene Symbol", "ENTREZ_GENE_ID"]
    assert len(df) == 2
    assert df.index.name == "ID"


def test_parse_gpl_table_lines_raises_data_error_when_no_table():
    parser = get_parser()
    with pytest.raises(DataError):
        parser.parse_gpl_table_lines(iter(["^PLATFORM = GPL1", "!Platform_title = x"]))


# --- 4. RNA-seq assembly resolution ------------------------------------------------------


def test_resolve_rnaseq_assembly_by_organism():
    from PyNCBI.domains.geo import resolve_rnaseq_assembly

    human = GSEInfo(gse_id="GSE1", organisms=["Homo sapiens"])
    mouse = GSEInfo(gse_id="GSE2", organisms=["Mus musculus"])
    mixed = GSEInfo(gse_id="GSE3", organisms=["Mus musculus", "Homo sapiens"])
    unknown = GSEInfo(gse_id="GSE4", organisms=["Danio rerio"])

    assert resolve_rnaseq_assembly(human) == "GRCh38.p13"
    assert resolve_rnaseq_assembly(mouse) == "GRCm39"
    assert resolve_rnaseq_assembly(mixed) == "GRCh38.p13"
    assert resolve_rnaseq_assembly(unknown) is None
    assert resolve_rnaseq_assembly(unknown, default="GRCh38.p13") == "GRCh38.p13"


def test_rnaseq_counts_url_builder_uses_assembly_and_count_type():
    from PyNCBI.domains.geo import rnaseq_counts_url

    url = rnaseq_counts_url("GSE152418", assembly="GRCm39", count_type="norm_counts_TPM")
    assert "acc=GSE152418" in url
    assert "file=GSE152418_norm_counts_TPM_GRCm39_NCBI.tsv.gz" in url


# --- 5. Gzip sanity in table loaders -------------------------------------------------------


def test_decompress_table_bytes_rejects_html_with_clear_error():
    from PyNCBI.domains.geo import decompress_table_bytes

    assert decompress_table_bytes(gzip.compress(b"a\tb\n1\t2\n")) == "a\tb\n1\t2\n"
    assert decompress_table_bytes(b"a\tb\n") == "a\tb\n"
    with pytest.raises(DataError, match="(?i)html"):
        decompress_table_bytes(b"<html><body>Not Found</body></html>")


# --- 6. Parallel multi-file download helper -----------------------------------------------


def test_download_urls_parallel_downloads_all_and_aggregates_failures(local_http, tmp_path):
    from PyNCBI.core.exceptions import DownloadError
    from PyNCBI.domains.geo import download_urls_parallel

    urls = [local_http.add(f"/suppl/file{i}.txt", f"content {i}") for i in range(5)]
    client = NCBIClient()

    paths = download_urls_parallel(client, urls, tmp_path, max_workers=3)
    assert sorted(p.name for p in paths) == [f"file{i}.txt" for i in range(5)]
    assert (tmp_path / "file3.txt").read_text() == "content 3"

    bad = urls + [local_http.url("/suppl/missing.txt")]
    with pytest.raises(DownloadError) as excinfo:
        download_urls_parallel(client, bad, tmp_path / "second", max_workers=3)
    assert "missing.txt" in str(excinfo.value)
    assert excinfo.value.completed_paths and len(excinfo.value.completed_paths) == 5


def test_bot_challenge_triggers_one_session_warm_up_and_retry(local_http, monkeypatch):
    from PyNCBI.core.config import Config

    monkeypatch.setattr(
        Config, "NCBI_QUERY_URL_TEMPLATE", local_http.base_url + "/query?acc={accession}"
    )
    local_http.add("/query?acc=GPL96", "^PLATFORM = GPL96")
    calls = {"n": 0}

    def _gated():
        calls["n"] += 1
        if calls["n"] == 1:
            return RECAPTCHA_HTML.encode(), "text/html"
        return gzip.compress(b"GeneID\tSymbol\n7157\tTP53\n"), "application/octet-stream"

    url = local_http.add_dynamic(
        "/geo/download/?type=rnaseq_counts&format=file&file=annot.tsv.gz", _gated
    )
    client = NCBIClient()
    body = client.get_bytes(url)
    assert gzip.decompress(body).startswith(b"GeneID")
    assert calls["n"] == 2
    assert any(h.startswith("/query?acc=GPL96") for h in local_http.hits)

    # A second challenge after warm-up is a hard error (no infinite retry)
    calls["n"] = 0
    with pytest.raises(BotChallengeError):
        client.get_bytes(url)


def test_stream_lines_also_recovers_from_bot_challenge_via_warm_up(local_http, monkeypatch):
    from PyNCBI.core.config import Config

    monkeypatch.setattr(
        Config, "NCBI_QUERY_URL_TEMPLATE", local_http.base_url + "/query?acc={accession}"
    )
    local_http.add("/query?acc=GPL96", "^PLATFORM = GPL96")
    calls = {"n": 0}

    def _gated():
        calls["n"] += 1
        return (
            (RECAPTCHA_HTML.encode(), "text/html") if calls["n"] == 1 else (b"a\nb\n", "text/plain")
        )

    url = local_http.add_dynamic("/geo/query/acc.cgi?acc=GPL570&view=data", _gated)
    assert list(NCBIClient().stream_lines(url)) == ["a", "b"]
