def test_import_package():
    """Test that the PyNCBI package imports correctly and has expected attributes."""
    import importlib

    PyNCBI = importlib.import_module("PyNCBI")

    # Core classes
    assert hasattr(PyNCBI, "GSM")
    assert hasattr(PyNCBI, "GSE")
    assert hasattr(PyNCBI, "GEOReader")

    # New architecture types
    assert hasattr(PyNCBI, "DataStatus")
    assert hasattr(PyNCBI, "FetchMode")
    assert hasattr(PyNCBI, "ArrayType")

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
    assert hasattr(PyNCBI, "DataError")
    assert hasattr(PyNCBI, "InvalidAccessionError")

    # Version
    assert hasattr(PyNCBI, "__version__")
    assert PyNCBI.__version__ == "0.2.0"
