"""Integration tests that download real data from NCBI.

These tests require network access and are slower than unit tests.
Run with: pytest tests/test_integration.py -v
Skip in CI with: pytest -m "not integration"
"""

import pandas as pd
import pytest

# Mark all tests in this module as integration tests
pytestmark = [pytest.mark.integration, pytest.mark.slow]


class TestGSMIntegration:
    """Integration tests for GSM class."""

    def test_gsm_shell_only_fetches_metadata(self):
        """Test that shell_only=True fetches metadata without data."""
        from PyNCBI import GSM

        # GSM1518180 is a small, stable sample used in documentation
        gsm = GSM("GSM1518180", shell_only=True)

        # Should have metadata
        assert gsm.info is not None
        assert isinstance(gsm.info, pd.Series)
        assert len(gsm.info) > 0

        # Should have parsed characteristics (can be dict or Series)
        assert gsm.characteristics is not None
        assert isinstance(gsm.characteristics, (dict, pd.Series))
        assert len(gsm.characteristics) > 0

        # Should have GSE parent
        assert gsm.gse is not None
        assert gsm.gse.startswith("GSE")

        # Should NOT have data (shell_only=True)
        assert gsm.data is None or (
            isinstance(gsm.data, str) and "Only Info" in gsm.data
        )

    def test_gsm_fetches_full_data(self):
        """Test that GSM fetches methylation data correctly."""
        from PyNCBI import GSM

        gsm = GSM("GSM1518180")

        # Should have metadata
        assert gsm.info is not None
        assert isinstance(gsm.info, pd.Series)

        # Should have data as DataFrame
        assert gsm.data is not None
        if isinstance(gsm.data, pd.DataFrame):
            assert len(gsm.data) > 0
            # Should have probe column or index
            assert "probe" in gsm.data.columns or gsm.data.index.name == "probe"

        # Should have array type
        assert gsm.array_type is not None

    def test_gsm_characteristics_parsing(self):
        """Test that characteristics are parsed correctly."""
        from PyNCBI import GSM

        gsm = GSM("GSM1518180", shell_only=True)

        chars = gsm.characteristics
        # Can be dict or Series depending on implementation
        assert isinstance(chars, (dict, pd.Series))

        # This sample should have common characteristics
        # (exact keys may vary, but should have something)
        assert len(chars) > 0


class TestGEOReaderIntegration:
    """Integration tests for GEOReader class."""

    def test_extract_gsm_info(self):
        """Test extracting GSM metadata via GEOReader."""
        from PyNCBI import GEOReader

        reader = GEOReader()
        info = reader.extract_gsm_info("GSM1518180")

        assert info is not None
        assert isinstance(info, pd.Series)
        assert len(info) > 0

    def test_list_gse_samples(self):
        """Test listing samples in a GSE."""
        from PyNCBI import GEOReader

        reader = GEOReader()
        # GSE62003 is a small series with known samples
        gsm_ids = reader.list_gse_samples("GSE62003")

        assert gsm_ids is not None
        assert isinstance(gsm_ids, list)
        assert len(gsm_ids) > 0
        assert all(gsm.startswith("GSM") for gsm in gsm_ids)

    def test_get_gsm_data_status(self):
        """Test checking data availability status."""
        from PyNCBI import GEOReader

        reader = GEOReader()
        status = reader.get_gsm_data_status("GSM1518180")

        # Should return 0 (data on page), 1 (IDAT files), or -1 (no data)
        assert status in (0, 1, -1)


class TestUtilitiesIntegration:
    """Integration tests for utility functions."""

    def test_gse_of_gsm(self):
        """Test finding parent GSE of a GSM."""
        from PyNCBI import gse_of_gsm

        result = gse_of_gsm("GSM1518180")

        # Should return GSE ID(s)
        if isinstance(result, str):
            assert result.startswith("GSE")
        else:
            assert isinstance(result, list)
            assert all(gse.startswith("GSE") for gse in result)

    def test_platform_of_gsm(self):
        """Test finding platform of a GSM."""
        from PyNCBI import platform_of_gsm

        result = platform_of_gsm("GSM1518180")

        # Should return GPL ID(s)
        if isinstance(result, str):
            assert result.startswith("GPL")
        else:
            assert isinstance(result, list)
            assert all(gpl.startswith("GPL") for gpl in result)


class TestExceptionHandling:
    """Test that exceptions are raised correctly for invalid inputs."""

    def test_invalid_accession_format(self):
        """Test that malformed accession raises InvalidAccessionError."""
        from PyNCBI._types import validate_accession
        from PyNCBI.exceptions import InvalidAccessionError

        with pytest.raises(InvalidAccessionError):
            validate_accession("INVALID", "GSM")

        with pytest.raises(InvalidAccessionError):
            validate_accession("GSM", "GSM")  # No digits

        with pytest.raises(InvalidAccessionError):
            validate_accession("ABC123", "GSE")  # Wrong prefix

        # Valid accessions should not raise
        assert validate_accession("GSM1234567", "GSM") == "GSM1234567"
        assert validate_accession("GSE12345", "GSE") == "GSE12345"
        assert validate_accession("GPL13534", "GPL") == "GPL13534"

        # Auto-detect valid prefixes
        assert validate_accession("gsm1234567", "") == "GSM1234567"  # lowercase OK

    def test_invalid_mode_error(self):
        """Test that invalid fetch mode raises InvalidModeError."""
        from PyNCBI._types import FetchMode

        with pytest.raises(ValueError, match="Invalid mode"):
            FetchMode.from_string("invalid_mode")
