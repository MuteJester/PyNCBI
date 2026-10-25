"""One-command study bundle (`build_study_bundle` / `pyncbi bundle` / MCP `ncbi_bundle`)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from native_support import cell, identifiers, read_csv_fixture, values
from PyNCBI.cli.main import main as cli_main
from PyNCBI.core.config import Config
from PyNCBI.domains.bundle import build_study_bundle
from PyNCBI.mcp.server import execute_mcp_tool

FIXTURES = Path(__file__).parent / "fixtures"

GSE_SOFT = """^SERIES = GSE100
!Series_title = Whole blood methylation in fibromyalgia
!Series_summary = We profiled DNA methylation in whole blood.
!Series_overall_design = Cases vs controls, Illumina 450k.
!Series_type = Methylation profiling by array
!Series_pubmed_id = 32433946
!Series_sample_organism = Homo sapiens
!Series_platform_id = GPL13534
!Series_sample_id = GSM1001
!Series_sample_id = GSM1002
!Series_relation = BioProject: https://www.ncbi.nlm.nih.gov/bioproject/PRJNA333
!Series_relation = SRA: https://www.ncbi.nlm.nih.gov/sra?term=SRP123456
!Series_supplementary_file = {base}/geo/series/GSE100/suppl/GSE100_RAW.tar
"""

BATCH_SOFT = """^SAMPLE = GSM1001
!Sample_title = Control_1
!Sample_series_id = GSE100
!Sample_platform_id = GPL13534
!Sample_characteristics_ch1 = tissue: Whole blood
!Sample_characteristics_ch1 = disease state: Control
!Sample_characteristics_ch1 = age: 45
^SAMPLE = GSM1002
!Sample_title = Case_1
!Sample_series_id = GSE100
!Sample_platform_id = GPL13534
!Sample_characteristics_ch1 = tissue: Whole blood
!Sample_characteristics_ch1 = disease state: Fibromyalgia
!Sample_characteristics_ch1 = age: 51
"""

PUBMED_XML = """<?xml version="1.0" ?><PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>32433946</PMID>
<Article><ArticleTitle>Methylation in fibromyalgia</ArticleTitle><Abstract><AbstractText>We profiled blood.</AbstractText></Abstract>
<Journal><Title>Cell Reports</Title><JournalIssue><PubDate><Year>2020</Year></PubDate></JournalIssue></Journal>
<AuthorList><Author><LastName>Doe</LastName><ForeName>J</ForeName></Author></AuthorList></Article></MedlineCitation>
<PubmedData><ArticleIdList><ArticleId IdType="pmc">PMC7236739</ArticleId><ArticleId IdType="doi">10.1/x</ArticleId></ArticleIdList></PubmedData>
</PubmedArticle></PubmedArticleSet>"""

RUNINFO = """Run,ReleaseDate,LoadDate,spots,bases,spots_with_mates,avgLength,size_MB,download_path,Experiment,LibraryName,LibraryStrategy,LibrarySelection,LibrarySource,LibraryLayout,Platform,Model,SRAStudy,BioProject,SampleName,ScientificName,BioSample
SRR1,2020-01-01,2020-01-01,1000,100000,0,100,12,https://x/SRR1,SRX1,lib1,Bisulfite-Seq,RANDOM,GENOMIC,PAIRED,ILLUMINA,NovaSeq,SRP123456,PRJNA333,GSM1001,Homo sapiens,SAMN1
SRR2,2020-01-01,2020-01-01,2000,200000,0,100,24,https://x/SRR2,SRX2,lib2,Bisulfite-Seq,RANDOM,GENOMIC,PAIRED,ILLUMINA,NovaSeq,SRP123456,PRJNA333,GSM1002,Homo sapiens,SAMN2
"""

MATRIX_TEXT = """!Series_title\t"Whole blood methylation"
!Series_geo_accession\t"GSE100"
!Series_platform_id\t"GPL13534"
!Sample_geo_accession\t"GSM1001"\t"GSM1002"
!Sample_characteristics_ch1\t"disease state: Control"\t"disease state: Fibromyalgia"
!series_matrix_table_begin
"ID_REF"\t"GSM1001"\t"GSM1002"
"cg00000029"\t0.51\t0.62
"cg00000108"\t0.87\t0.80
!series_matrix_table_end
"""


@pytest.fixture
def fake_study(local_http, monkeypatch):
    base = local_http.base_url
    monkeypatch.setattr(Config, "NCBI_QUERY_URL_TEMPLATE", base + "/query?acc={accession}")
    monkeypatch.setattr(Config, "NCBI_BATCH_GSM_URL_TEMPLATE", base + "/batch?acc={accession}")
    monkeypatch.setattr(Config, "NCBI_EUTILS_BASE_URL", base + "/eutils")
    monkeypatch.setattr(Config, "NCBI_FTP_HTTPS_BASE", base)
    monkeypatch.setattr(Config, "NCBI_GEO_DOWNLOAD_URL", base + "/geo/download/")
    local_http.add("/query?acc=GSE100", GSE_SOFT.format(base=base))
    local_http.add("/batch?acc=GSE100", BATCH_SOFT)
    local_http.add("/geo/series/GSE100/suppl/GSE100_RAW.tar", b"x" * 2048, "application/x-tar")
    local_http.add_gzip("/geo/series/GSEnnn/GSE100/matrix/GSE100_series_matrix.txt.gz", MATRIX_TEXT)
    local_http.add("/eutils/efetch.fcgi?db=pubmed&id=32433946&retmode=xml", PUBMED_XML, "text/xml")
    local_http.add(
        "/eutils/elink.fcgi?dbfrom=pubmed&db=pmc&id=32433946&retmode=json",
        json.dumps(
            {
                "linksets": [
                    {
                        "linksetdbs": [
                            {"dbto": "pmc", "linkname": "pubmed_pmc", "links": ["7236739"]}
                        ]
                    }
                ]
            }
        ),
        "application/json",
    )
    local_http.add(
        "/eutils/efetch.fcgi?db=pmc&id=PMC7236739&retmode=xml",
        (FIXTURES / "pmc_sample.xml").read_text(),
        "text/xml",
    )
    local_http.add(
        "/eutils/esearch.fcgi?db=sra&term=SRP123456&retmax=500&retstart=0&retmode=json",
        json.dumps({"esearchresult": {"count": "2", "idlist": ["1", "2"]}}),
        "application/json",
    )
    local_http.add(
        "/eutils/efetch.fcgi?db=sra&id=1%2C2&rettype=runinfo&retmode=text", RUNINFO, "text/csv"
    )
    return local_http


def test_build_study_bundle_writes_complete_self_contained_directory(fake_study, tmp_path):
    out = tmp_path / "GSE100_bundle"
    bundle = build_study_bundle("GSE100", out)

    assert bundle.gse_id == "GSE100"
    assert bundle.output_dir == out
    assert bundle.sample_count == 2
    expected = {
        "overview",
        "samples",
        "manifest",
        "bundle_json",
        "record",
        "sra",
        "pubmed",
        "methods",
    }
    assert expected <= set(bundle.files)
    for key in expected:
        assert bundle.files[key].exists(), key

    overview = (out / "STUDY_OVERVIEW.md").read_text()
    assert "Whole blood methylation in fibromyalgia" in overview
    assert "GPL13534" in overview
    assert "disease state" in overview and "Fibromyalgia (1)" in overview
    assert "GSE100_RAW.tar" in overview and "2.0 KB" in overview
    assert "SRP123456" in overview or "SRR1" in overview
    assert "PMID:32433946" in overview or "32433946" in overview
    assert "Ficoll" in overview  # methods excerpt from PMC
    assert "pyncbi download GSE100" in overview

    samples = read_csv_fixture(out / "samples_metadata.csv")
    assert list(identifiers(samples)) == ["GSM1001", "GSM1002"]
    assert cell(samples, "GSM1002", "disease state") == "Fibromyalgia"

    sra = read_csv_fixture(out / "sra_runs.csv")
    assert set(values(sra, "Run")) == {"SRR1", "SRR2"}

    pubmed = json.loads((out / "pubmed_articles.json").read_text())
    assert pubmed[0]["pmid"] == "32433946"
    assert pubmed[0]["full_text"]["pmcid"] == "PMC7236739"
    methods_md = (out / "paper_methods.md").read_text()
    assert "STAR Methods" in methods_md and "GSE152418" in methods_md

    manifest = json.loads((out / "files_manifest.json").read_text())
    assert any(f["name"] == "GSE100_RAW.tar" for f in manifest)
    study = json.loads((out / "bundle.json").read_text())
    assert study["report"]["accession"] == "GSE100"
    assert study["bundle"]["files"]["overview"].endswith("STUDY_OVERVIEW.md")
    record = json.loads((out / "study.json").read_text())
    assert (
        record["record_type"] == "study"
        and record["samples"][1]["harmonized"]["disease"]["value"] == "Fibromyalgia"
    )
    assert bundle.warnings == []
    assert "matrix" not in bundle.files


def test_build_study_bundle_with_matrix_and_cohort_filter(fake_study, tmp_path):
    out = tmp_path / "b2"
    bundle = build_study_bundle(
        "GSE100",
        out,
        include_matrix=True,
        where="disease state: Fibromyalgia",
        include_sra=False,
        include_methods=False,
    )

    assert bundle.sample_count == 1
    samples = read_csv_fixture(out / "samples_metadata.csv")
    assert list(identifiers(samples)) == ["GSM1002"]
    assert "sra" not in bundle.files
    assert bundle.files["matrix"].name == "GSE100_series_matrix.csv"
    matrix = read_csv_fixture(bundle.files["matrix"])
    assert cell(matrix, "cg00000029", "GSM1002") == pytest.approx(0.62)
    pubmed = json.loads((out / "pubmed_articles.json").read_text())
    assert pubmed[0]["full_text"] is None


def test_bundle_is_robust_to_optional_step_failures(fake_study, tmp_path):
    # Break SRA: esearch route removed -> 404 -> recorded as warning, bundle still produced
    fake_study.routes.pop(
        "/eutils/esearch.fcgi?db=sra&term=SRP123456&retmax=500&retstart=0&retmode=json"
    )
    bundle = build_study_bundle("GSE100", tmp_path / "b3")
    assert "sra" not in bundle.files
    assert any("SRA" in w for w in bundle.warnings)
    assert (tmp_path / "b3" / "STUDY_OVERVIEW.md").exists()


def test_cli_bundle_and_mcp_bundle(fake_study, tmp_path, capsys):
    rc = cli_main(["bundle", "GSE100", "-o", str(tmp_path / "cli"), "--no-sra", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["data"]["sample_count"] == 2
    assert any(p.endswith("STUDY_OVERVIEW.md") for p in payload["saved_files"])

    out = execute_mcp_tool(
        "ncbi_bundle",
        {"gse_id": "GSE100", "output_dir": str(tmp_path / "mcp"), "include_sra": False},
    )
    assert "STUDY_OVERVIEW.md" in out and "GSE100" in out
