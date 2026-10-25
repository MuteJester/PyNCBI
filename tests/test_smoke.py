def test_import_package():
    """Test that the PyNCBI package imports fast, without warnings, and has expected attributes."""
    import importlib

    PyNCBI = importlib.import_module("PyNCBI")

    # Core classes & backend client
    assert hasattr(PyNCBI, "GSM")
    assert hasattr(PyNCBI, "GSE")
    assert hasattr(PyNCBI, "GEOReader")
    assert hasattr(PyNCBI, "NCBIClient")
    assert hasattr(PyNCBI, "get_client")

    # High-level domain functions
    assert hasattr(PyNCBI, "inspect_accession")
    assert hasattr(PyNCBI, "extract_gse_samples")
    assert hasattr(PyNCBI, "load_series_matrix")
    assert hasattr(PyNCBI, "load_rnaseq_counts")
    assert hasattr(PyNCBI, "download_geo_files")
    assert hasattr(PyNCBI, "search_geo")
    assert hasattr(PyNCBI, "search_pubmed")
    assert hasattr(PyNCBI, "fetch_pubmed")
    assert hasattr(PyNCBI, "search_gene")
    assert hasattr(PyNCBI, "fetch_genes")
    assert hasattr(PyNCBI, "fetch_sra_runs")
    assert hasattr(PyNCBI, "fetch_fasta")
    assert hasattr(PyNCBI, "preview_geo_file")
    assert hasattr(PyNCBI, "link_accession")

    # Architecture types
    assert hasattr(PyNCBI, "DataStatus")
    assert hasattr(PyNCBI, "FetchMode")

    # Configuration
    assert hasattr(PyNCBI, "Config")
    assert hasattr(PyNCBI, "get_config")
    assert hasattr(PyNCBI, "set_config")

    # Logging
    assert hasattr(PyNCBI, "LogLevel")
    assert hasattr(PyNCBI, "configure_logging")
    assert hasattr(PyNCBI, "silence")
    assert hasattr(PyNCBI, "verbose")

    # Exceptions
    assert hasattr(PyNCBI, "PyNCBIError")
    assert hasattr(PyNCBI, "NetworkError")
    assert hasattr(PyNCBI, "RateLimitExceededError")
    assert hasattr(PyNCBI, "InvalidAPIKeyError")
    assert hasattr(PyNCBI, "AccessionNotFoundError")
    assert hasattr(PyNCBI, "AccessionPrivateError")
    assert hasattr(PyNCBI, "DataError")
    assert hasattr(PyNCBI, "InvalidAccessionError")

    # Version
    assert hasattr(PyNCBI, "__version__")
    assert PyNCBI.__version__ == "1.0.0"
