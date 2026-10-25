"""Parsing package for PyNCBI.

Contains parsers for NCBI GEO SOFT format and Series Matrix files.
"""

from PyNCBI.parsing.gds_parser import GDSResult, parse_gds_soft
from PyNCBI.parsing.matrix_parser import SeriesMatrixParser, SeriesMatrixResult
from PyNCBI.parsing.pmc_parser import classify_section_title, parse_pmc_article_xml
from PyNCBI.parsing.soft_parser import SOFTParser, get_parser, parse_characteristics

__all__ = [
    "SOFTParser",
    "get_parser",
    "parse_characteristics",
    "SeriesMatrixParser",
    "SeriesMatrixResult",
    "parse_pmc_article_xml",
    "GDSResult",
    "parse_gds_soft",
    "classify_section_title",
]
