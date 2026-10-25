"""FASTQ resolution (ENA file report) and verified parallel FASTQ downloads."""

from __future__ import annotations

import hashlib
import json

import pytest

from PyNCBI.cli.main import main as cli_main
from PyNCBI.core.config import Config
from PyNCBI.core.exceptions import DownloadError
from PyNCBI.domains.sequencing import download_fastq, fetch_fastq_urls, parse_ena_file_report

ENA_ROWS = [
    {
        "run_accession": "SRR11957478",
        "sample_accession": "SAMN15180526",
        "experiment_accession": "SRX8500105",
        "study_accession": "PRJNA638153",
        "secondary_sample_accession": "SRS6798783",
        "fastq_ftp": "ftp.sra.ebi.ac.uk/vol1/fastq/SRR119/078/SRR11957478/SRR11957478_1.fastq.gz;ftp.sra.ebi.ac.uk/vol1/fastq/SRR119/078/SRR11957478/SRR11957478_2.fastq.gz",
        "fastq_bytes": "115964304;131175082",
        "fastq_md5": "3476ad2a61c7179f3c5451fc45ac9653;9e3e1a7b9c984ac3791dd8d21e2439d2",
        "sra_ftp": "",
        "sra_bytes": "",
        "library_layout": "PAIRED",
        "library_strategy": "OTHER",
        "read_count": "1778895",
        "base_count": "516320135",
        "scientific_name": "Mus musculus",
        "instrument_model": "Illumina NovaSeq 6000",
        "sample_alias": "GSM4598302",
        "sample_title": "PIT_P5",
    },
    {
        "run_accession": "SRR12007824",
        "sample_accession": "SAMN15300001",
        "experiment_accession": "SRX8540001",
        "study_accession": "PRJNA639275",
        "secondary_sample_accession": "SRS6830001",
        "fastq_ftp": "ftp.sra.ebi.ac.uk/vol1/fastq/SRR120/024/SRR12007824/SRR12007824.fastq.gz",
        "fastq_bytes": "507379799",
        "fastq_md5": "0123456789abcdef0123456789abcdef",
        "sra_ftp": "",
        "sra_bytes": "",
        "library_layout": "SINGLE",
        "library_strategy": "RNA-Seq",
        "read_count": "100",
        "base_count": "10000",
        "scientific_name": "Homo sapiens",
        "instrument_model": "Illumina NovaSeq 6000",
        "sample_alias": "GSM4614986",
        "sample_title": "S147",
    },
]


def test_parse_ena_file_report_expands_paired_files_into_rows():
    df = parse_ena_file_report(ENA_ROWS)
    assert list(df.columns[:6]) == [
        "run_accession",
        "sample_alias",
        "file_name",
        "file_url",
        "size_bytes",
        "md5",
    ]
    assert len(df) == 3
    first = df.iloc[0]
    assert (
        first["file_url"]
        == "https://ftp.sra.ebi.ac.uk/vol1/fastq/SRR119/078/SRR11957478/SRR11957478_1.fastq.gz"
    )
    assert first["size_bytes"] == 115964304 and first["md5"] == "3476ad2a61c7179f3c5451fc45ac9653"
    assert (
        first["read_index"] == 1 and df.iloc[1]["read_index"] == 2 and df.iloc[2]["read_index"] == 0
    )
    assert first["sample_alias"] == "GSM4598302" and first["library_layout"] == "PAIRED"
    assert df.iloc[2]["study_accession"] == "PRJNA639275"


@pytest.fixture
def fake_ena(local_http, monkeypatch):
    base = local_http.base_url
    monkeypatch.setattr(Config, "ENA_PORTAL_API_URL", base + "/ena/portal/api")
    monkeypatch.setattr(Config, "NCBI_QUERY_URL_TEMPLATE", base + "/query?acc={accession}")
    monkeypatch.setattr(Config, "NCBI_BATCH_GSM_URL_TEMPLATE", base + "/batch?acc={accession}")
    fields = "run_accession,sample_accession,secondary_sample_accession,experiment_accession,study_accession,sample_alias,sample_title,scientific_name,library_strategy,library_layout,instrument_model,read_count,base_count,fastq_ftp,fastq_bytes,fastq_md5,sra_ftp,sra_bytes"
    for acc, rows in (
        ("PRJNA639275", [ENA_ROWS[1]]),
        ("SRX8500105", [ENA_ROWS[0]]),
        ("SRR12007824", [ENA_ROWS[1]]),
    ):
        local_http.add(
            f"/ena/portal/api/filereport?accession={acc}&result=read_run&fields={fields.replace(',', '%2C')}&format=json&limit=0",
            json.dumps(rows),
            "application/json",
        )
    local_http.add(
        "/query?acc=GSE152418",
        "^SERIES = GSE152418\n!Series_title = t\n!Series_sample_id = GSM4614986\n!Series_relation = BioProject: https://www.ncbi.nlm.nih.gov/bioproject/PRJNA639275\n!Series_relation = SRA: https://www.ncbi.nlm.nih.gov/sra?term=SRP267176\n",
    )
    local_http.add(
        "/query?acc=GSM4598302",
        "^SAMPLE = GSM4598302\n!Sample_title = PIT\n!Sample_series_id = GSE1\n!Sample_relation = SRA: https://www.ncbi.nlm.nih.gov/sra?term=SRX8500105\n",
    )
    return local_http


def test_fetch_fastq_urls_resolves_gse_gsm_and_run_accessions(fake_ena):
    gse = fetch_fastq_urls("GSE152418")
    assert gse.iloc[0]["sample_alias"] == "GSM4614986"
    gsm = fetch_fastq_urls("GSM4598302")
    assert set(gsm["run_accession"]) == {"SRR11957478"} and len(gsm) == 2
    run = fetch_fastq_urls("SRR12007824")
    assert run.iloc[0]["file_name"] == "SRR12007824.fastq.gz"


def test_download_fastq_verifies_md5_and_reports_mismatch(fake_ena, tmp_path):
    good = b"@read1\nACGT\n+\nIIII\n"
    fake_ena.add(
        "/vol1/fastq/SRR120/024/SRR12007824/SRR12007824.fastq.gz", good, "application/octet-stream"
    )
    ok_md5 = hashlib.md5(good).hexdigest()
    rows = [
        dict(
            ENA_ROWS[1],
            fastq_md5=ok_md5,
            fastq_bytes=str(len(good)),
            fastq_ftp=f"{fake_ena.base_url}/vol1/fastq/SRR120/024/SRR12007824/SRR12007824.fastq.gz",
        )
    ]
    import PyNCBI.domains.sequencing as seq

    df = seq.parse_ena_file_report(rows)
    paths = download_fastq(df, tmp_path / "fq", max_workers=2)
    assert [p.name for p in paths] == ["SRR12007824.fastq.gz"]
    assert paths[0].read_bytes() == good

    bad = seq.parse_ena_file_report([dict(rows[0], fastq_md5="deadbeefdeadbeefdeadbeefdeadbeef")])
    with pytest.raises(DownloadError, match="(?i)md5"):
        download_fastq(bad, tmp_path / "fq_bad")


def test_cli_fastq_lists_and_saves_manifest(fake_ena, tmp_path, capsys):
    rc = cli_main(["fastq", "SRR12007824", "--json", "-o", str(tmp_path / "fastq.csv")])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["file_count"] == 1
    assert payload["data"]["total_size_human"]
    assert (tmp_path / "fastq.csv").exists()
