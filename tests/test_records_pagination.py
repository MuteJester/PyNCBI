"""E-utilities pagination (limit=None) and structured records for BioSample, BioProject,
Nucleotide/Protein, and Taxonomy."""

from __future__ import annotations

import json

from PyNCBI.cli.main import main as cli_main
from PyNCBI.core.config import Config
from PyNCBI.domains.entrez import (
    esearch_ids,
    esummary_docs,
    fetch_bioprojects,
    fetch_biosamples,
    fetch_taxonomy,
    parse_bioproject_summary,
    parse_biosample_summary,
    parse_nucleotide_summary,
    parse_taxonomy_summary,
    search_ncbi,
    search_taxonomy,
)

BIOSAMPLE_DOC = {
    "uid": "15180526",
    "title": "PIT_P5-PIT_P6-F8-AD002",
    "accession": "SAMN15180526",
    "date": "2021/02/24",
    "taxonomy": "10090",
    "organism": "Mus musculus",
    "identifiers": "BioSample: SAMN15180526; SRA: SRS6798783; GEO: GSM4598302",
    "package": "Generic",
    "sampledata": (
        '<BioSample access="public" publication_date="2021-02-24T00:00:00.000" id="15180526" accession="SAMN15180526">'
        '<Ids><Id db="BioSample" is_primary="1">SAMN15180526</Id><Id db="SRA">SRS6798783</Id><Id db="GEO">GSM4598302</Id></Ids>'
        '<Description><Title>PIT_P5-PIT_P6-F8-AD002</Title><Organism taxonomy_id="10090" taxonomy_name="Mus musculus"><OrganismName>Mus musculus</OrganismName></Organism></Description>'
        "<Owner><Name>Ecker lab, Salk Institute</Name></Owner>"
        '<Attributes><Attribute attribute_name="source_name" harmonized_name="source_name">C57BL/6J Male P56 Mouse Brain</Attribute>'
        '<Attribute attribute_name="tissue" harmonized_name="tissue">Pituitary</Attribute>'
        '<Attribute attribute_name="age" harmonized_name="age">P56</Attribute>'
        '<Attribute attribute_name="Sex" harmonized_name="sex">male</Attribute></Attributes></BioSample>'
    ),
}
BIOPROJECT_DOC = {
    "uid": "639275",
    "taxid": 9606,
    "project_id": 639275,
    "project_acc": "PRJNA639275",
    "project_type": "Primary submission",
    "project_data_type": "Transcriptome or Gene expression",
    "project_target_scope": "Multiisolate",
    "project_target_material": "Transcriptome",
    "project_methodtype": "Sequencing",
    "registration_date": "2020/06/13 00:00",
    "project_name": "Systems biological assessment",
    "project_title": "Systems biological assessment of immunity",
    "project_description": "The recent emergence ...",
    "organism_name": "Homo sapiens",
    "organism_strain": "",
    "sort_by_projecttype": 1,
    "sort_by_datatype": 2,
    "relevance_medical": "yes",
    "keyword": "",
}
NUCCORE_DOC = {
    "uid": "1732746264",
    "caption": "NM_007294",
    "title": "Homo sapiens BRCA1 DNA repair associated (BRCA1), transcript variant 1, mRNA",
    "extra": "gi|1732746264|ref|NM_007294.4|",
    "gi": 1732746264,
    "createdate": "1999/10/17",
    "updatedate": "2026/09/10",
    "taxid": 9606,
    "slen": 7088,
    "biomol": "mRNA",
    "moltype": "rna",
    "topology": "linear",
    "sourcedb": "refseq",
    "completeness": "complete",
    "organism": "Homo sapiens",
    "accessionversion": "NM_007294.4",
}
TAX_DOC = {
    "uid": "9606",
    "status": "active",
    "rank": "species",
    "division": "primates",
    "scientificname": "Homo sapiens",
    "commonname": "human",
    "taxid": 9606,
    "genbankdivision": "Primates",
}
TAX_XML = """<?xml version="1.0" ?><TaxaSet><Taxon><TaxId>9606</TaxId><ScientificName>Homo sapiens</ScientificName>
<OtherNames><GenbankCommonName>human</GenbankCommonName></OtherNames><ParentTaxId>9605</ParentTaxId><Rank>species</Rank>
<Division>Primates</Division><GeneticCode><GCId>1</GCId><GCName>Standard</GCName></GeneticCode>
<MitoGeneticCode><MGCId>2</MGCId><MGCName>Vertebrate Mitochondrial</MGCName></MitoGeneticCode>
<Lineage>cellular organisms; Eukaryota; Metazoa; Chordata; Mammalia; Primates; Hominidae; Homo</Lineage>
<LineageEx><Taxon><TaxId>2759</TaxId><ScientificName>Eukaryota</ScientificName><Rank>superkingdom</Rank></Taxon>
<Taxon><TaxId>40674</TaxId><ScientificName>Mammalia</ScientificName><Rank>class</Rank></Taxon></LineageEx></Taxon></TaxaSet>"""


def test_parse_biosample_summary_unpacks_attributes_and_links():
    rec = parse_biosample_summary(BIOSAMPLE_DOC)
    assert rec.accession == "SAMN15180526" and rec.uid == "15180526"
    assert rec.organism == "Mus musculus" and rec.taxid == "10090"
    assert rec.gsm_id == "GSM4598302" and rec.sra_sample == "SRS6798783"
    assert rec.owner == "Ecker lab, Salk Institute"
    assert rec.attributes == {
        "source_name": "C57BL/6J Male P56 Mouse Brain",
        "tissue": "Pituitary",
        "age": "P56",
        "sex": "male",
    }
    d = rec.to_dict()
    assert "sampledata" not in d and d["attributes"]["tissue"] == "Pituitary"


def test_parse_bioproject_nucleotide_taxonomy_summaries():
    bp = parse_bioproject_summary(BIOPROJECT_DOC)
    assert bp.accession == "PRJNA639275" and bp.data_type == "Transcriptome or Gene expression"
    assert bp.organism == "Homo sapiens" and bp.registration_date == "2020/06/13"
    assert "sort_by_projecttype" not in bp.to_dict()

    nt = parse_nucleotide_summary(NUCCORE_DOC, db="nuccore")
    assert nt.accession == "NM_007294.4" and nt.length == 7088 and nt.moltype == "rna"
    assert nt.source_db == "refseq" and nt.organism == "Homo sapiens" and nt.db == "nuccore"

    tx = parse_taxonomy_summary(TAX_DOC, xml_text=TAX_XML)
    assert tx.taxid == "9606" and tx.scientific_name == "Homo sapiens" and tx.common_name == "human"
    assert tx.rank == "species" and tx.genetic_code == "1" and tx.mito_genetic_code == "2"
    assert tx.lineage[-1] == "Homo" and tx.lineage[0] == "cellular organisms"
    assert tx.lineage_ranks["class"] == "Mammalia"
    assert tx.parent_taxid == "9605"


def test_esearch_ids_paginates_with_retstart_and_esummary_batches(local_http, monkeypatch):
    monkeypatch.setattr(Config, "NCBI_EUTILS_BASE_URL", local_http.base_url + "/eutils")
    all_ids = [str(i) for i in range(1, 1201)]

    # esearch pages (page_size=1000): retstart 0 and 1000
    for retstart in (0, 1000):
        key = f"/eutils/esearch.fcgi?db=biosample&term=tissue+pituitary&retmax=1000&retstart={retstart}&retmode=json"
        page = all_ids[retstart : retstart + 1000]
        local_http.add(
            key,
            json.dumps(
                {
                    "esearchresult": {
                        "count": "1200",
                        "retmax": "1000",
                        "retstart": str(retstart),
                        "idlist": page,
                    }
                }
            ),
            "application/json",
        )
    ids, total = esearch_ids("biosample", "tissue pituitary", limit=None)
    assert total == 1200 and ids == all_ids

    # limit smaller than a page -> single request with retmax=limit
    local_http.add(
        "/eutils/esearch.fcgi?db=biosample&term=tissue+pituitary&retmax=7&retstart=0&retmode=json",
        json.dumps({"esearchresult": {"count": "1200", "idlist": all_ids[:7]}}),
        "application/json",
    )
    ids7, total7 = esearch_ids("biosample", "tissue pituitary", limit=7)
    assert ids7 == all_ids[:7] and total7 == 1200

    # esummary batches of 500 (3 POST-promoted requests for 1200 ids); the local server treats POST like GET
    for start in (0, 500, 1000):
        batch = all_ids[start : start + 500]
        result = {
            "uids": batch,
            **{
                u: {"uid": u, "accession": f"SAMN{u}", "title": f"s{u}", "sampledata": ""}
                for u in batch
            },
        }
        local_http.add(
            f"/eutils/esummary.fcgi?db=biosample&id={'%2C'.join(batch)}&retmode=json",
            json.dumps({"result": result}),
            "application/json",
        )
    docs = esummary_docs("biosample", all_ids)
    assert len(docs) == 1200 and docs["1200"]["accession"] == "SAMN1200"
    assert sum(1 for h in local_http.hits if h.startswith("/eutils/esummary")) == 3


def test_search_ncbi_returns_clean_records_for_known_dbs(local_http, monkeypatch):
    monkeypatch.setattr(Config, "NCBI_EUTILS_BASE_URL", local_http.base_url + "/eutils")
    local_http.add(
        "/eutils/esearch.fcgi?db=biosample&term=SAMN15180526&retmax=20&retstart=0&retmode=json",
        json.dumps({"esearchresult": {"count": "1", "idlist": ["15180526"]}}),
        "application/json",
    )
    local_http.add(
        "/eutils/esummary.fcgi?db=biosample&id=15180526&retmode=json",
        json.dumps({"result": {"uids": ["15180526"], "15180526": BIOSAMPLE_DOC}}),
        "application/json",
    )
    recs = search_ncbi("biosample", "SAMN15180526")
    assert recs[0]["accession"] == "SAMN15180526" and recs[0]["attributes"]["tissue"] == "Pituitary"
    raw = search_ncbi("biosample", "SAMN15180526", raw=True)
    assert "sampledata" in raw[0]

    local_http.add(
        "/eutils/esearch.fcgi?db=biosample&term=SAMN15180526&retmax=5&retstart=0&retmode=json",
        json.dumps({"esearchresult": {"count": "1", "idlist": ["15180526"]}}),
        "application/json",
    )
    samples = fetch_biosamples(["SAMN15180526"])
    assert samples[0].gsm_id == "GSM4598302"

    local_http.add(
        "/eutils/esearch.fcgi?db=bioproject&term=PRJNA639275%5BProject+Accession%5D&retmax=5&retstart=0&retmode=json",
        json.dumps({"esearchresult": {"count": "1", "idlist": ["639275"]}}),
        "application/json",
    )
    local_http.add(
        "/eutils/esummary.fcgi?db=bioproject&id=639275&retmode=json",
        json.dumps({"result": {"uids": ["639275"], "639275": BIOPROJECT_DOC}}),
        "application/json",
    )
    bps = fetch_bioprojects(["PRJNA639275"])
    assert bps[0].accession == "PRJNA639275" and bps[0].title.startswith("Systems")


def test_taxonomy_lookup_by_id_and_name_and_cli(local_http, monkeypatch, capsys):
    monkeypatch.setattr(Config, "NCBI_EUTILS_BASE_URL", local_http.base_url + "/eutils")
    local_http.add(
        "/eutils/esummary.fcgi?db=taxonomy&id=9606&retmode=json",
        json.dumps({"result": {"uids": ["9606"], "9606": TAX_DOC}}),
        "application/json",
    )
    local_http.add("/eutils/efetch.fcgi?db=taxonomy&id=9606&retmode=xml", TAX_XML, "text/xml")
    local_http.add(
        "/eutils/esearch.fcgi?db=taxonomy&term=human%5BAll+Names%5D&retmax=5&retstart=0&retmode=json",
        json.dumps({"esearchresult": {"count": "1", "idlist": ["9606"]}}),
        "application/json",
    )

    by_id = fetch_taxonomy(["9606"])
    assert (
        by_id[0].scientific_name == "Homo sapiens" and by_id[0].lineage_ranks["class"] == "Mammalia"
    )
    by_name = search_taxonomy("human", limit=5)
    assert by_name[0].taxid == "9606"

    rc = cli_main(["taxonomy", "human", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["data"][0]["common_name"] == "human"
