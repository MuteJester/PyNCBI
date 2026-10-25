"""Structured ClinVar & dbSNP variant lookup."""

from __future__ import annotations

import json

import pytest

from PyNCBI.cli.main import main as cli_main
from PyNCBI.core.config import Config
from PyNCBI.domains.variants import (
    build_clinvar_term,
    fetch_clinvar,
    fetch_dbsnp,
    fetch_variants,
    parse_clinvar_summary,
    parse_dbsnp_summary,
    search_clinvar,
)
from PyNCBI.mcp.server import execute_mcp_tool

CLINVAR_DOC = {
    "uid": "5019327",
    "obj_type": "Insertion",
    "accession": "VCV005019327",
    "accession_version": "VCV005019327.1",
    "title": "NM_007294.4(BRCA1):c.5296_5297insC (p.Ile1766fs)",
    "variation_set": [
        {
            "measure_id": "5129633",
            "variation_xrefs": [{"db_source": "dbSNP", "db_id": "80357906"}],
            "variation_name": "NM_007294.4(BRCA1):c.5296_5297insC (p.Ile1766fs)",
            "cdna_change": "c.5296_5297insC",
            "variation_loc": [
                {
                    "status": "current",
                    "assembly_name": "GRCh38",
                    "chr": "17",
                    "band": "17q21.31",
                    "start": "43051098",
                    "stop": "43051099",
                    "ref": "",
                    "alt": "",
                },
                {
                    "status": "previous",
                    "assembly_name": "GRCh37",
                    "chr": "17",
                    "band": "17q21.31",
                    "start": "41203115",
                    "stop": "41203116",
                },
            ],
            "variant_type": "Insertion",
            "canonical_spdi": "NC_000017.11:43051098::G",
        }
    ],
    "supporting_submissions": {"scv": ["SCV008101219"], "rcv": ["RCV007092891"]},
    "germline_classification": {
        "description": "Pathogenic",
        "last_evaluated": "2023/05/12 00:00",
        "review_status": "criteria provided, single submitter",
        "trait_set": [
            {
                "trait_xrefs": [
                    {"db_source": "MedGen", "db_id": "C2676676"},
                    {"db_source": "OMIM", "db_id": "604370"},
                ],
                "trait_name": "Breast-ovarian cancer, familial, susceptibility to, 1",
            }
        ],
    },
    "clinical_impact_classification": {"description": "", "review_status": "", "trait_set": []},
    "oncogenicity_classification": {"description": "", "review_status": "", "trait_set": []},
    "gene_sort": "BRCA1",
    "chr_sort": "17",
    "genes": [{"symbol": "BRCA1", "geneid": "672", "strand": "-", "source": "submitted"}],
    "molecular_consequence_list": ["frameshift variant", "intron variant"],
    "protein_change": "I1469fs, I1766fs, I354fs",
}

DBSNP_DOC = {
    "uid": "7412",
    "snp_id": 7412,
    "global_mafs": [
        {"study": "1000Genomes", "freq": "T=0.0750799/376"},
        {"study": "GnomAD_exomes", "freq": "T=0.0740017/98492"},
        {"study": "ALFA", "freq": "T=0.0830989/15476"},
    ],
    "clinical_significance": "drug-response,risk-factor,benign,pathogenic",
    "genes": [{"name": "APOE", "gene_id": "348"}],
    "acc": "NC_000019.10",
    "chr": "19",
    "spdi": "NC_000019.10:44908821:C:T",
    "fxn_class": "missense_variant,coding_sequence_variant",
    "validated": "by-frequency,by-alfa,by-cluster",
    "docsum": "HGVS=NC_000019.10:g.44908822C>T,NM_000041.4:c.526C>T,NP_000032.1:p.Arg176Cys|SEQ=[C/T]|LEN=1|GENE=APOE:348",
    "chrpos": "19:44908822",
    "chrpos_prev_assm": "19:45412079",
}


def test_parse_clinvar_summary_flattens_noisy_esummary_into_record():
    rec = parse_clinvar_summary(CLINVAR_DOC)
    assert rec.variation_id == "5019327"
    assert rec.accession == "VCV005019327"
    assert rec.title.startswith("NM_007294.4(BRCA1)")
    assert rec.gene_symbol == "BRCA1"
    assert rec.gene_id == "672"
    assert rec.variant_type == "Insertion"
    assert rec.cdna_change == "c.5296_5297insC"
    assert rec.protein_change == "I1469fs, I1766fs, I354fs"
    assert rec.clinical_significance == "Pathogenic"
    assert rec.review_status == "criteria provided, single submitter"
    assert rec.review_stars == 1
    assert rec.last_evaluated == "2023/05/12"
    assert rec.conditions == ["Breast-ovarian cancer, familial, susceptibility to, 1"]
    assert rec.molecular_consequences == ["frameshift variant", "intron variant"]
    assert rec.rsid == "rs80357906"
    assert rec.grch38 == {"chr": "17", "start": 43051098, "stop": 43051099, "band": "17q21.31"}
    assert rec.grch37["start"] == 41203115
    assert rec.canonical_spdi == "NC_000017.11:43051098::G"
    assert rec.url == "https://www.ncbi.nlm.nih.gov/clinvar/variation/5019327/"
    d = rec.to_dict()
    assert d["grch38_location"] == "chr17:43051098-43051099"
    assert "variation_set" not in d


def test_review_stars_mapping():
    from PyNCBI.domains.variants import review_status_to_stars

    assert review_status_to_stars("practice guideline") == 4
    assert review_status_to_stars("reviewed by expert panel") == 3
    assert review_status_to_stars("criteria provided, multiple submitters, no conflicts") == 2
    assert review_status_to_stars("criteria provided, conflicting classifications") == 1
    assert review_status_to_stars("no assertion criteria provided") == 0
    assert review_status_to_stars("") == 0


def test_parse_dbsnp_summary():
    rec = parse_dbsnp_summary(DBSNP_DOC)
    assert rec.rsid == "rs7412"
    assert rec.chromosome == "19"
    assert rec.position_grch38 == 44908822
    assert rec.position_grch37 == 45412079
    assert rec.spdi == "NC_000019.10:44908821:C:T"
    assert rec.ref_allele == "C" and rec.alt_alleles == ["T"]
    assert rec.gene_symbols == ["APOE"]
    assert rec.functional_class == ["missense_variant", "coding_sequence_variant"]
    assert "pathogenic" in rec.clinical_significance
    assert rec.hgvs[:2] == ["NC_000019.10:g.44908822C>T", "NM_000041.4:c.526C>T"]
    assert rec.allele_frequencies["GnomAD_exomes"] == pytest.approx(0.0740017)
    assert rec.url == "https://www.ncbi.nlm.nih.gov/snp/rs7412"
    assert rec.to_dict()["protein_change"] == "p.Arg176Cys"


def test_build_clinvar_term_for_gene_rsid_hgvs_and_free_text():
    assert build_clinvar_term("BRCA1") == "BRCA1[gene]"
    assert (
        build_clinvar_term("brca1", significance="pathogenic")
        == 'BRCA1[gene] AND "pathogenic"[clinsig]'
    )
    assert build_clinvar_term("rs7412") == "rs7412[rs]"
    assert build_clinvar_term("NM_007294.4:c.5266dupC") == '"NM_007294.4:c.5266dupC"[varname]'
    assert build_clinvar_term("BRCA1 c.5266dupC") == "(BRCA1 c.5266dupC)"
    assert (
        build_clinvar_term("TP53", significance="likely pathogenic", gene="TP53")
        == 'TP53[gene] AND "likely pathogenic"[clinsig]'
    )
    assert build_clinvar_term("breast cancer", gene="BRCA2") == "(breast cancer) AND BRCA2[gene]"


@pytest.fixture
def fake_eutils(local_http, monkeypatch):
    monkeypatch.setattr(Config, "NCBI_EUTILS_BASE_URL", local_http.base_url + "/eutils")
    local_http.add(
        "/eutils/esearch.fcgi?db=clinvar&term=BRCA1%5Bgene%5D+AND+%22pathogenic%22%5Bclinsig%5D&retmax=5&retstart=0&retmode=json",
        json.dumps({"esearchresult": {"count": "1", "idlist": ["5019327"]}}),
        "application/json",
    )
    local_http.add(
        "/eutils/esummary.fcgi?db=clinvar&id=5019327&retmode=json",
        json.dumps({"result": {"uids": ["5019327"], "5019327": CLINVAR_DOC}}),
        "application/json",
    )
    local_http.add(
        "/eutils/esummary.fcgi?db=snp&id=7412&retmode=json",
        json.dumps({"result": {"uids": ["7412"], "7412": DBSNP_DOC}}),
        "application/json",
    )
    local_http.add(
        "/eutils/esearch.fcgi?db=clinvar&term=rs7412%5Brs%5D&retmax=20&retstart=0&retmode=json",
        json.dumps({"esearchresult": {"count": "0", "idlist": []}}),
        "application/json",
    )
    return local_http


def test_search_clinvar_and_fetch_clinvar(fake_eutils):
    recs = search_clinvar("BRCA1", significance="pathogenic", limit=5)
    assert len(recs) == 1 and recs[0].gene_symbol == "BRCA1"
    recs2 = fetch_clinvar(["5019327"])
    assert recs2[0].accession == "VCV005019327"


def test_fetch_dbsnp_and_fetch_variants_auto(fake_eutils):
    snps = fetch_dbsnp("rs7412")
    assert snps[0].gene_symbols == ["APOE"]

    result = fetch_variants("rs7412")  # auto: rsID -> dbSNP + ClinVar
    assert result["query"] == "rs7412"
    assert result["dbsnp"][0]["rsid"] == "rs7412"
    assert result["clinvar"] == []


def test_cli_variant_json_and_mcp_tool(fake_eutils, capsys):
    rc = cli_main(["variant", "BRCA1", "--significance", "pathogenic", "-n", "5", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["data"]["clinvar"][0]["clinical_significance"] == "Pathogenic"

    rc = cli_main(["clinvar", "BRCA1", "--significance", "pathogenic", "-n", "5", "--markdown"])
    out = capsys.readouterr().out
    assert rc == 0 and "VCV005019327" in out

    mcp_out = json.loads(execute_mcp_tool("ncbi_variant", {"query": "rs7412"}))
    assert mcp_out["data"]["dbsnp"][0]["gene_symbols"] == ["APOE"]
