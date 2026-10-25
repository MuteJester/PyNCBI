"""PMC full-text retrieval: section parsing, Methods / Data Availability extraction,
PMID<->PMCID resolution, CLI `--methods`, and MCP `ncbi_pubmed` sections."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from PyNCBI.cli.main import main as cli_main
from PyNCBI.core.config import Config
from PyNCBI.domains.literature import fetch_pmc_text, resolve_pmc_id
from PyNCBI.mcp.server import execute_mcp_tool
from PyNCBI.parsing.pmc_parser import classify_section_title, parse_pmc_article_xml

FIXTURES = Path(__file__).parent / "fixtures"
OPEN_XML = (FIXTURES / "pmc_sample.xml").read_text()
CLOSED_XML = (FIXTURES / "pmc_closed.xml").read_text()

PUBMED_XML = """<?xml version="1.0" ?><PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>32433946</PMID>
<Article><ArticleTitle>Single-cell RNA expression of SARS-CoV-2 entry factors</ArticleTitle>
<Abstract><AbstractText>We analysed PBMCs.</AbstractText></Abstract>
<Journal><Title>Cell Reports</Title><JournalIssue><PubDate><Year>2020</Year></PubDate></JournalIssue></Journal>
<AuthorList><Author><LastName>Singh</LastName><ForeName>M</ForeName></Author></AuthorList></Article></MedlineCitation>
<PubmedData><ArticleIdList><ArticleId IdType="pmc">PMC7236739</ArticleId><ArticleId IdType="doi">10.1016/x</ArticleId></ArticleIdList></PubmedData>
</PubmedArticle></PubmedArticleSet>"""


# --- Pure parser ---------------------------------------------------------------------------


def test_classify_section_title_recognizes_common_headings():
    assert classify_section_title("STAR Methods") == "methods"
    assert classify_section_title("Materials and Methods") == "methods"
    assert classify_section_title("Experimental Procedures") == "methods"
    assert classify_section_title("Online Methods") == "methods"
    assert classify_section_title("Results") == "results"
    assert classify_section_title("Results and Discussion") == "results"
    assert classify_section_title("Discussion") == "discussion"
    assert classify_section_title("Data and Code Availability") == "data_availability"
    assert classify_section_title("Availability of data and materials") == "data_availability"
    assert classify_section_title("Introduction") == "introduction"
    assert classify_section_title("Background") == "introduction"
    assert classify_section_title("Supplementary Material") == "supplementary"
    assert classify_section_title("Cohort overview") == "other"


def test_parse_pmc_article_xml_extracts_metadata_and_nested_sections():
    art = parse_pmc_article_xml(OPEN_XML)

    assert art.pmcid == "PMC7236739"
    assert art.pmid == "32433946"
    assert art.doi == "10.1016/j.celrep.2020.107774"
    assert art.title == "Single-cell RNA expression of SARS-CoV-2 entry factors"
    assert art.journal == "Cell Reports"
    assert art.pub_year == "2020"
    assert art.is_open_access is True

    titles = [s.title for s in art.sections]
    assert titles[:3] == ["Introduction", "Results", "Cohort overview"]
    methods = [s for s in art.sections if s.kind == "methods"]
    # Parent 'STAR Methods' plus its three subsections inherit kind='methods'
    assert [s.title for s in methods] == [
        "STAR Methods",
        "Sample processing",
        "RNA-seq data processing",
        "Patient classification",
    ]
    proc = next(s for s in methods if s.title == "RNA-seq data processing")
    assert proc.level == 2
    assert proc.path == "STAR Methods > RNA-seq data processing"
    assert "aligned to GRCh38 with STAR v2.7" in proc.text
    assert "ignored table cell" not in proc.text
    # Inline xref labels are dropped from text, italics kept as text
    intro = art.sections[0]
    assert (
        intro.text == "Severe acute respiratory syndrome coronavirus 2 (SARS-CoV-2) spreads fast."
    )

    da = [s for s in art.sections if s.kind == "data_availability"]
    assert len(da) == 1 and "GSE152418" in da[0].text
    assert "GSE152418" in art.data_availability
    assert "Ficoll" in art.methods
    assert art.geo_accessions == ["GSE152418"]


def test_parse_pmc_article_xml_closed_access_has_no_sections():
    art = parse_pmc_article_xml(CLOSED_XML)
    assert art.pmcid == "PMC4000000"
    assert art.is_open_access is False
    assert art.sections == []
    assert "not available" in art.notes.lower()


def test_full_text_markdown_and_section_filtering():
    art = parse_pmc_article_xml(OPEN_XML)
    md = art.to_markdown(sections=["methods", "data_availability"])
    assert "## STAR Methods" in md
    assert "### RNA-seq data processing" in md
    assert "Data and Code Availability" in md
    assert "Introduction" not in md

    only = art.filter_sections(["sample proc"])
    assert [s.title for s in only] == ["Sample processing"]
    d = art.to_dict(sections=["methods"])
    assert all(sec["kind"] == "methods" for sec in d["sections"])
    assert d["pmcid"] == "PMC7236739"


# --- Network layer through local server ----------------------------------------------------


@pytest.fixture
def fake_eutils(local_http, monkeypatch):
    base = local_http.base_url
    monkeypatch.setattr(Config, "NCBI_EUTILS_BASE_URL", base + "/eutils")
    local_http.add("/eutils/efetch.fcgi?db=pmc&id=PMC7236739&retmode=xml", OPEN_XML, "text/xml")
    local_http.add("/eutils/efetch.fcgi?db=pmc&id=PMC4000000&retmode=xml", CLOSED_XML, "text/xml")
    local_http.add(
        "/eutils/elink.fcgi?dbfrom=pubmed&db=pmc&id=32433946&retmode=json",
        json.dumps(
            {
                "linksets": [
                    {
                        "dbfrom": "pubmed",
                        "ids": ["32433946"],
                        "linksetdbs": [
                            {"dbto": "pmc", "linkname": "pubmed_pmc", "links": ["7236739"]}
                        ],
                    }
                ]
            }
        ),
        "application/json",
    )
    local_http.add(
        "/eutils/elink.fcgi?dbfrom=pubmed&db=pmc&id=11111&retmode=json",
        json.dumps({"linksets": [{"dbfrom": "pubmed", "ids": ["11111"]}]}),
        "application/json",
    )
    local_http.add("/eutils/efetch.fcgi?db=pubmed&id=32433946&retmode=xml", PUBMED_XML, "text/xml")
    return local_http


def test_resolve_pmc_id_from_pmid_and_pmcid(fake_eutils):
    assert resolve_pmc_id("32433946") == ("32433946", "PMC7236739")
    assert resolve_pmc_id("PMID:32433946") == ("32433946", "PMC7236739")
    assert resolve_pmc_id("PMC7236739") == ("", "PMC7236739")
    assert resolve_pmc_id("pmc7236739") == ("", "PMC7236739")
    assert resolve_pmc_id("11111") == ("11111", "")


def test_fetch_pmc_text_by_pmid_resolves_and_parses(fake_eutils):
    art = fetch_pmc_text("32433946", sections=["methods"])
    assert art.pmcid == "PMC7236739"
    assert art.pmid == "32433946"
    # Filtering is a view: every section is retained, the filter drives output selection
    assert len(art.sections) == 10
    assert all(s.kind == "methods" for s in art.selected_sections())
    assert all(s["kind"] == "methods" for s in art.to_dict()["sections"])
    assert art.to_dict()["section_count"] == 10
    assert "STAR" in art.methods
    none = art.to_dict(sections=["zzz-no-such-section"])
    assert none["sections"] == [] and "Introduction" in none["available_sections"]


def test_fetch_pmc_text_without_pmc_record_returns_not_open_access(fake_eutils):
    art = fetch_pmc_text("11111")
    assert art.is_open_access is False
    assert art.pmid == "11111"
    assert "no pmc" in art.notes.lower()


def test_cli_pubmed_methods_flag_appends_methods_sections(fake_eutils, capsys):
    rc = cli_main(["pubmed", "32433946", "--methods", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    art = payload["data"][0]
    assert art["pmid"] == "32433946"
    assert art["full_text"]["pmcid"] == "PMC7236739"
    kinds = {s["kind"] for s in art["full_text"]["sections"]}
    assert kinds == {"methods", "data_availability"}

    rc = cli_main(["pubmed", "32433946", "--fulltext", "--markdown"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "## Introduction" in out and "### RNA-seq data processing" in out


def test_mcp_pubmed_tool_supports_sections(fake_eutils):
    out = execute_mcp_tool("ncbi_pubmed", {"pmids_or_query": "32433946", "sections": ["methods"]})
    payload = json.loads(out)
    art = payload["data"][0]
    assert art["full_text"]["pmcid"] == "PMC7236739"
    assert {s["kind"] for s in art["full_text"]["sections"]} == {"methods"}


def test_data_availability_falls_back_to_scanning_acknowledgments_sentences():
    xml = """<pmc-articleset><article><front><article-meta>
<article-id pub-id-type="pmc">PMC1</article-id><title-group><article-title>Science style</article-title></title-group>
</article-meta></front>
<body><p>Opening paragraph without a heading.</p>
<sec><title>Severe disease</title><p>Findings.</p></sec></body>
<back><ack><title>Acknowledgments</title><p>We thank everyone. Funding: NIH grants. Author contributions: all.
Competing interests: none. Data and materials availability: RNA-seq data are deposited in GEO under accession GSE152418.
Other data are in the supplementary materials.</p></ack></back></article></pmc-articleset>"""
    art = parse_pmc_article_xml(xml)
    assert art.sections[0].kind == "introduction" and art.sections[0].title == ""
    da = art.data_availability
    assert "GSE152418" in da and "deposited in GEO" in da
    assert "We thank everyone" not in da
    assert art.geo_accessions == ["GSE152418"]
