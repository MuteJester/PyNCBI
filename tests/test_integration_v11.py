"""Live NCBI integration tests for the 1.1 capabilities (annotation, cohort downloads,
PMC full text, study bundles, variants). Network required: `pytest -m integration`."""

from __future__ import annotations

import pandas as pd
import pytest

import PyNCBI

pytestmark = [pytest.mark.integration, pytest.mark.slow]


def test_fetch_gpl_table_streams_with_limit_and_columns():
    df = PyNCBI.fetch_gpl_table(
        "GPL570", limit=5, columns=["Gene Symbol", "ENTREZ_GENE_ID"], use_cache=False
    )
    assert df.index.name == "ID"
    assert len(df) == 5
    assert df.loc["1053_at", "Gene Symbol"] == "RFC2"


def test_rnaseq_counts_annotate_adds_symbols_without_touching_counts():
    plain = PyNCBI.load_rnaseq_counts("GSE152418")
    annotated = PyNCBI.load_rnaseq_counts("GSE152418", annotate=True)
    assert list(annotated.columns[:3]) == ["Symbol", "GeneType", "EnsemblGeneID"]
    assert annotated["Symbol"].notna().mean() > 0.9
    sample = plain.columns[0]
    pd.testing.assert_series_equal(annotated[sample], plain[sample], check_names=True)
    assert annotated.loc["7157", "Symbol"] == "TP53"


def test_mouse_annotation_resolves_through_fallback_cascade():
    table = PyNCBI.load_rnaseq_annotation("GRCm39", gene_ids=["22059", "12043"])
    assert table.loc["22059", "Symbol"] == "Trp53"
    assert table.loc["12043", "GeneType"] == "protein-coding"


def test_cohort_resolution_is_single_request_and_filters(tmp_path):
    client = PyNCBI.get_client()
    before = client.metrics.summary()["total_requests"]
    files = PyNCBI.resolve_cohort_files("GSE85506", where="title: 1", pattern="*_Grn.idat.gz")
    assert client.metrics.summary()["total_requests"] - before == 1
    assert files and all(f.file_name.endswith("_Grn.idat.gz") for f in files)
    gsms = {f.gsm_id for f in files}
    assert len(gsms) == len(files)  # one Grn file per matching sample


def test_fetch_pmc_text_methods_for_open_access_paper():
    art = PyNCBI.fetch_pmc_text("PMC7263504", sections=["methods", "data_availability"])
    assert art.is_open_access
    assert art.pmcid == "PMC7263504"
    assert art.methods
    assert any(s.kind == "methods" for s in art.selected_sections())


def test_fetch_pmc_text_closed_article_is_flagged():
    art = PyNCBI.fetch_pmc_text("PMC4000000")
    assert art.is_open_access is False
    assert art.notes


def test_search_clinvar_pathogenic_brca1_and_dbsnp_rs7412():
    recs = PyNCBI.search_clinvar("BRCA1", significance="pathogenic", limit=3)
    assert recs and all(r.gene_symbol == "BRCA1" for r in recs)
    assert all("athogenic" in r.clinical_significance for r in recs)
    assert all(r.grch38.get("chr") == "17" for r in recs)

    exact = PyNCBI.search_clinvar("NM_007294.4:c.5266dupC", limit=3)
    assert exact and exact[0].variation_id == "17677"

    snp = PyNCBI.fetch_dbsnp("rs7412")[0]
    assert snp.gene_symbols == ["APOE"]
    assert snp.position_grch38 == 44908822
    assert snp.protein_change == "p.Arg176Cys"


def test_build_study_bundle_live(tmp_path):
    bundle = PyNCBI.build_study_bundle("GSE152418", tmp_path / "GSE152418")
    assert bundle.sample_count == 34
    assert bundle.sra_run_count > 0
    assert bundle.methods_available is True
    overview = bundle.files["overview"].read_text()
    assert "GSE152418" in overview and "severity" in overview
    assert (tmp_path / "GSE152418" / "sra_runs.csv").exists()
    assert bundle.warnings == []


def test_load_expression_resolves_ncbi_counts_for_sequencing_series():
    res = PyNCBI.load_expression("GSE152418")
    assert res.source == "ncbi_rnaseq_counts"
    assert res.matrix_df.shape == (39376, 34)
    assert res.matrix_df.index.name == "GeneID"
    assert all(str(c).startswith("GSM") for c in res.matrix_df.columns)
    assert list(res.samples_df.index) == [str(c) for c in res.matrix_df.columns]
    assert res.candidates[0]["source"] == "ncbi_rnaseq_counts"


def test_load_expression_resolves_series_matrix_for_methylation_array():
    res = PyNCBI.load_expression("GSE85506")
    assert res.source == "series_matrix"
    assert res.matrix_df.shape[1] == 47 and res.matrix_df.shape[0] > 400_000
    assert res.matrix_df.index.name == "probe"


def test_load_expression_cohort_filter_and_size_guard():
    res = PyNCBI.load_expression("GSE152418", where="severity == 'Severe'")
    assert res.matrix_df.shape[1] == 8 and len(res.samples_df) == 8

    with pytest.raises(PyNCBI.DataError, match="max_download_mb"):
        PyNCBI.load_expression("GSE152418", max_download_mb=0.001)


def test_load_expression_falls_back_when_preferred_source_is_empty():
    res = PyNCBI.load_expression("GSE152418", prefer="matrix")
    assert [c["source"] for c in res.candidates] == ["series_matrix", "ncbi_rnaseq_counts"]
    assert res.source == "ncbi_rnaseq_counts"


def test_build_study_record_live_harmonization_and_quality():
    rec = PyNCBI.build_study_record("GSE152418", check_file_sizes=False)
    assert rec.sample_count == 34 and rec.quality is not None and rec.quality.score == 100
    assert {"sex", "disease", "cell_type", "time_point"} <= set(rec.harmonization["mapped_fields"])
    first = rec.samples[0]
    assert first.harmonized["sex"].value in ("male", "female")
    assert first.harmonized["sex"].provenance.rule == "sex.synonym"
    assert first.links["biosample"].startswith("SAMN")
    assert rec.links["sra_study"].startswith("SRP")


def test_screen_studies_live_comparison_table():
    res = PyNCBI.screen_studies(["GSE152418", "GSE164805"], max_workers=2, check_file_sizes=False)
    assert list(res.table.index) == ["GSE152418", "GSE164805"]
    assert res.table.loc["GSE152418", "sample_count"] == 34
    assert res.table.loc["GSE152418", "sex_coverage"] == 1.0
    assert "sex" in res.table.loc["GSE152418", "harmonized_fields"]
    assert res.errors == {}


def test_screen_studies_live_reports_bad_accession_without_aborting():
    res = PyNCBI.screen_studies(["GSE164805", "GSE999999999"], check_file_sizes=False)
    assert list(res.table.index) == ["GSE164805"]
    assert res.errors["GSE999999999"]["error_code"] == "not_found"


def test_download_plan_and_budget_guard_live(tmp_path):
    plan = PyNCBI.plan_geo_download("GSE85506", tmp_path / "raw", source="supp")
    assert plan.file_count == 1 and plan.files[0]["name"] == "GSE85506_RAW.tar"
    assert plan.total_bytes > 500_000_000 and plan.fits_on_disk in (True, False)
    assert not (tmp_path / "raw").exists()

    with pytest.raises(PyNCBI.DataError, match="max_download_mb"):
        PyNCBI.download_geo_files("GSE85506", tmp_path / "raw", source="supp", max_download_mb=1)


def test_blast_lifecycle_live():
    """Submit a real BLAST search, poll it, and read the hits (skips if NCBI is slow today)."""
    query = (
        "AGCTCGCTGAGACTTCCTGGACCCCGCACCAGGCTGTGGGGTTTCTCAGATAACTGGGCCCC"
        "TGCGCTCAGGAGGCCTTCACCCTCTGCTCTGGGTAAAG"
    )
    job = PyNCBI.submit_blast(query, program="blastn", database="nt", hitlist_size=5)
    assert job.rid and job.status == "waiting" and job.program == "blastn"
    assert job.rid in job.url
    try:
        status = PyNCBI.check_blast(job.rid)
        assert status.status in ("waiting", "ready")
        result = PyNCBI.fetch_blast(job.rid) if status.is_ready else None
        if result is None:
            result = PyNCBI.blast(
                query, program="blastn", hitlist_size=5, poll_interval=12, timeout=240
            )
    except TimeoutError as exc:
        PyNCBI.delete_blast(job.rid)
        pytest.skip(f"NCBI BLAST was still running: {exc}")

    assert result.hit_count > 0
    assert result.program == "blastn" and result.query_len == 100
    top = result.hits[0]
    assert top.accession and top.bit_score and top.evalue is not None
    assert top.percent_identity and top.percent_identity > 80
    assert "BRCA1" in " ".join(h.title for h in result.hits).upper()
    assert list(result.hits_df().columns)[:3] == ["hit_num", "title", "length"]
    PyNCBI.delete_blast(job.rid)
