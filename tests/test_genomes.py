"""NCBI Datasets: assembly search, report, and safe genome package download."""

from __future__ import annotations

import io
import json
import zipfile

import pytest

from PyNCBI.cli.main import main as cli_main
from PyNCBI.core.config import Config
from PyNCBI.core.exceptions import PathTraversalError
from PyNCBI.domains.genomes import (
    download_genome,
    fetch_assembly,
    parse_assembly_report,
    search_assemblies,
)

REPORT = {
    "accession": "GCF_000005845.2",
    "current_accession": "GCF_000005845.2",
    "paired_accession": "GCA_000005845.2",
    "source_database": "SOURCE_DATABASE_REFSEQ",
    "organism": {
        "tax_id": 511145,
        "organism_name": "Escherichia coli str. K-12 substr. MG1655",
        "infraspecific_names": {"strain": "K-12 substr. MG1655"},
    },
    "assembly_info": {
        "assembly_level": "Complete Genome",
        "assembly_status": "current",
        "assembly_name": "ASM584v2",
        "assembly_type": "haploid",
        "release_date": "2013-09-26",
        "submitter": "Univ. Wisconsin",
        "refseq_category": "reference genome",
        "biosample": {"accession": "SAMN02604091"},
        "bioproject_accession": "PRJNA57779",
    },
    "assembly_stats": {
        "total_sequence_length": "4641652",
        "contig_n50": 4641652,
        "scaffold_n50": 4641652,
        "number_of_component_sequences": 1,
        "total_number_of_chromosomes": 1,
        "gc_percent": 50.5,
    },
    "annotation_info": {
        "name": "GCF_000005845.2-RS_2024_08",
        "release_date": "2024-08-28",
        "stats": {"gene_counts": {"total": 4639, "protein_coding": 4298}},
    },
}


def test_parse_assembly_report_flattens_datasets_json():
    rec = parse_assembly_report(REPORT)
    assert rec.accession == "GCF_000005845.2" and rec.paired_accession == "GCA_000005845.2"
    assert rec.organism == "Escherichia coli str. K-12 substr. MG1655" and rec.taxid == "511145"
    assert rec.assembly_name == "ASM584v2" and rec.assembly_level == "Complete Genome"
    assert rec.refseq_category == "reference genome" and rec.source_db == "refseq"
    assert rec.total_length == 4641652 and rec.contig_n50 == 4641652 and rec.chromosome_count == 1
    assert rec.gene_count == 4639 and rec.protein_coding_count == 4298
    assert rec.annotation_name.startswith("GCF_000005845.2-RS")
    assert rec.to_dict()["url"].endswith("GCF_000005845.2/")


@pytest.fixture
def fake_datasets(local_http, monkeypatch):
    base = local_http.base_url
    monkeypatch.setattr(Config, "NCBI_DATASETS_BASE_URL", base + "/datasets/v2")
    local_http.add(
        "/datasets/v2/genome/taxon/Escherichia%20coli/dataset_report?page_size=5&filters.reference_only=true",
        json.dumps({"reports": [REPORT], "total_count": 1}),
        "application/json",
    )
    local_http.add(
        "/datasets/v2/genome/accession/GCF_000005845.2/dataset_report",
        json.dumps({"reports": [REPORT]}),
        "application/json",
    )
    local_http.add(
        "/datasets/v2/genome/accession/GCF_000005845.2/download_summary?include_annotation_type=GENOME_FASTA%2CGENOME_GFF",
        json.dumps({"record_count": 1, "hydrated": {"estimated_file_size_mb": 3}}),
        "application/json",
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "ncbi_dataset/data/GCF_000005845.2/GCF_000005845.2_ASM584v2_genomic.fna", ">chr\nACGT\n"
        )
        zf.writestr("ncbi_dataset/data/GCF_000005845.2/genomic.gff", "##gff-version 3\n")
        zf.writestr("ncbi_dataset/data/assembly_data_report.jsonl", json.dumps(REPORT))
        zf.writestr("README.md", "readme")
    local_http.add(
        "/datasets/v2/genome/accession/GCF_000005845.2/download?include_annotation_type=GENOME_FASTA%2CGENOME_GFF&hydrated=FULLY_HYDRATED",
        buf.getvalue(),
        "application/zip",
    )
    return local_http


def test_search_and_fetch_assembly(fake_datasets):
    hits = search_assemblies("Escherichia coli", limit=5)
    assert hits[0].accession == "GCF_000005845.2"
    rec = fetch_assembly("GCF_000005845.2")
    assert rec.organism.startswith("Escherichia")


def test_download_genome_unpacks_zip_safely_and_returns_data_files(fake_datasets, tmp_path):
    result = download_genome("GCF_000005845.2", tmp_path / "ecoli", include=("genome", "gff3"))
    names = sorted(p.name for p in result.files)
    assert names == [
        "GCF_000005845.2_ASM584v2_genomic.fna",
        "assembly_data_report.jsonl",
        "genomic.gff",
    ]
    assert result.estimated_size_mb == 3
    assert (
        (tmp_path / "ecoli" / "GCF_000005845.2_ASM584v2_genomic.fna").read_text().startswith(">chr")
    )
    assert not (tmp_path / "ecoli" / "GCF_000005845.2.zip").exists()  # archive removed after unpack


def test_zip_extraction_blocks_path_traversal(tmp_path):
    from PyNCBI.io.compression import extract_zipfile

    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("../evil.txt", "x")
    with pytest.raises(PathTraversalError):
        extract_zipfile(bad, tmp_path / "out")


def test_cli_genome_search_json(fake_datasets, capsys):
    rc = cli_main(["genome", "search", "Escherichia coli", "-n", "5", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["data"][0]["accession"] == "GCF_000005845.2"
