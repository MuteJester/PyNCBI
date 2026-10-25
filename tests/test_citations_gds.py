"""Citation export (BibTeX / RIS) and GEO DataSet (GDS) value tables."""

from __future__ import annotations

import json

import pytest

from PyNCBI.cli.main import main as cli_main
from PyNCBI.core.config import Config
from PyNCBI.core.models import PubMedArticle
from PyNCBI.domains.citations import export_citations, to_bibtex, to_ris
from PyNCBI.domains.geo import inspect_accession, load_gds
from PyNCBI.parsing.gds_parser import parse_gds_soft

ART = PubMedArticle(
    pmid="32788292",
    title="Systems biological assessment of immunity to mild versus severe COVID-19 infection in humans",
    abstract="Abstract text.",
    authors=["Arunachalam PS", "Wimmers F", "Pulendran B"],
    journal="Science",
    pub_date="2020 Sep",
    doi="10.1126/science.abc6261",
    pmc_id="PMC7402624",
    keywords=["COVID-19"],
)


def test_bibtex_and_ris_rendering():
    bib = to_bibtex(ART)
    assert bib.startswith("@article{Arunachalam2020,")
    assert "author = {Arunachalam, PS and Wimmers, F and Pulendran, B}" in bib
    assert "journal = {Science}" in bib and "year = {2020}" in bib
    assert (
        "doi = {10.1126/science.abc6261}" in bib
        and "pmid = {32788292}" in bib
        and "pmcid = {PMC7402624}" in bib
    )

    ris = to_ris(ART)
    assert ris.startswith("TY  - JOUR\n")
    assert (
        "AU  - Arunachalam, PS\n" in ris
        and "PY  - 2020\n" in ris
        and "DO  - 10.1126/science.abc6261\n" in ris
    )
    assert ris.rstrip().endswith("ER  -")


def test_export_citations_writes_files(tmp_path):
    bib_path = export_citations([ART, ART], tmp_path / "refs.bib")
    text = bib_path.read_text()
    assert text.count("@article{") == 2
    # Duplicate keys get a suffix
    assert "@article{Arunachalam2020," in text and "@article{Arunachalam2020a," in text
    ris_path = export_citations([ART], tmp_path / "refs.ris")
    assert ris_path.read_text().count("TY  - JOUR") == 1
    assert export_citations([ART], None, fmt="bibtex").startswith("@article")


GDS_SOFT = """^DATABASE = Geo
^DATASET = GDS1234
!dataset_title = FGFR3 K650E mutation
!dataset_description = Analysis of PC12 cells.
!dataset_type = Expression profiling by array
!dataset_pubmed_id = 15843401
!dataset_platform = GPL85
!dataset_platform_organism = Rattus norvegicus
!dataset_feature_count = 3
!dataset_sample_count = 4
!dataset_value_type = count
!dataset_reference_series = GSE2606
^SUBSET = GDS1234_1
!subset_dataset_id = GDS1234
!subset_description = wild type
!subset_sample_id = GSM49962,GSM49963
!subset_type = genotype/variation
^SUBSET = GDS1234_2
!subset_dataset_id = GDS1234
!subset_description = FGFR3 K650E
!subset_sample_id = GSM49958,GSM49959
!subset_type = genotype/variation
^DATASET = GDS1234
#ID_REF = Platform reference identifier
#IDENTIFIER = identifier
#GSM49962 = Value for GSM49962
!dataset_table_begin
ID_REF\tIDENTIFIER\tGSM49962\tGSM49963\tGSM49958\tGSM49959
AFFX-1\tGapdh\t100.5\t101\t200\t199.5
AFFX-2\tActb\t50\t51\t52\t53
AFFX-3\tnull\tnull\t1\t2\t3
!dataset_table_end
"""


def test_parse_gds_soft_builds_table_subsets_and_sample_groups():
    res = parse_gds_soft(GDS_SOFT.splitlines(), gds_id="GDS1234")
    assert res.gds_id == "GDS1234" and res.info["title"] == "FGFR3 K650E mutation"
    assert res.info["reference_series"] == "GSE2606" and res.info["platform"] == "GPL85"
    assert res.table_df.index.name == "ID_REF"
    assert list(res.table_df.columns) == [
        "IDENTIFIER",
        "GSM49962",
        "GSM49963",
        "GSM49958",
        "GSM49959",
    ]
    assert res.table_df.loc["AFFX-1", "GSM49959"] == pytest.approx(199.5)
    assert res.table_df["GSM49962"].dtype.kind == "f"
    assert res.subsets[1]["description"] == "FGFR3 K650E" and res.subsets[1]["sample_ids"] == [
        "GSM49958",
        "GSM49959",
    ]
    assert res.sample_groups_df.loc["GSM49958", "genotype/variation"] == "FGFR3 K650E"
    assert res.expression_df().columns.tolist() == ["GSM49962", "GSM49963", "GSM49958", "GSM49959"]


@pytest.fixture
def fake_gds(local_http, monkeypatch):
    base = local_http.base_url
    monkeypatch.setattr(Config, "NCBI_FTP_HTTPS_BASE", base)
    monkeypatch.setattr(Config, "NCBI_EUTILS_BASE_URL", base + "/eutils")
    local_http.add_gzip("/geo/datasets/GDS1nnn/GDS1234/soft/GDS1234.soft.gz", GDS_SOFT)
    local_http.add(
        "/eutils/esummary.fcgi?db=gds&id=1234&retmode=json",
        json.dumps(
            {
                "result": {
                    "uids": ["1234"],
                    "1234": {
                        "uid": "1234",
                        "accession": "GDS1234",
                        "title": "FGFR3 K650E mutation",
                        "summary": "Analysis of PC12 cells.",
                        "gpl": "85",
                        "gse": "2606",
                        "taxon": "Rattus norvegicus",
                        "entrytype": "GDS",
                        "gdstype": "Expression profiling by array",
                        "n_samples": 4,
                        "pubmedids": [15843401],
                        "ftplink": "ftp://ftp.ncbi.nlm.nih.gov/geo/datasets/GDS1nnn/GDS1234/",
                    },
                }
            }
        ),
        "application/json",
    )
    return local_http


def test_load_gds_and_inspect_gds(fake_gds, tmp_path):
    res = load_gds("GDS1234", save_to=tmp_path / "gds.csv")
    assert res.table_df.shape == (3, 5)
    assert (tmp_path / "gds.csv").exists()

    report = inspect_accession("GDS1234", check_file_sizes=True)
    assert report.record_type == "GDS" and report.title == "FGFR3 K650E mutation"
    assert (
        report.platforms == ["GPL85"]
        and report.parent_gse == "GSE2606"
        and report.sample_count == 4
    )
    assert report.pubmed_ids == ["15843401"]
    assert any(f.source == "gds_soft" for f in report.files)


def test_cli_pubmed_bibtex_and_gds_command(fake_gds, tmp_path, capsys, monkeypatch):
    out = export_citations([ART], None, fmt="ris")
    assert "TY  - JOUR" in out
    rc = cli_main(["gds", "GDS1234", "--json", "-o", str(tmp_path / "t.csv")])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert (
        payload["data"]["shape"] == [3, 5]
        and payload["data"]["subsets"][0]["type"] == "genotype/variation"
    )


def test_citations_use_pubmed_surname_split_for_multiword_names():
    art = PubMedArticle(
        pmid="1",
        title="T",
        authors=["Mok Chris Ka Pun", "Arunachalam Prabhu S"],
        journal="J",
        pub_date="2021",
        author_details=[
            {"last": "Mok", "fore": "Chris Ka Pun", "initials": "CKP", "affiliation": ""},
            {"last": "Arunachalam Prabhu", "fore": "S", "initials": "S", "affiliation": ""},
        ],
    )
    bib = to_bibtex(art)
    assert "author = {Mok, Chris Ka Pun and Arunachalam Prabhu, S}" in bib
    assert bib.startswith("@article{Mok2021,")
    assert "AU  - Mok, Chris Ka Pun\n" in to_ris(art)
