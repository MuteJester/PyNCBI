"""Parsing layer for PyNCBI.

This package contains modules for parsing various data formats:
- SOFT format from NCBI GEO
- IDAT files (via methylprep)
"""

from PyNCBI.parsing.soft_parser import SOFTParser

__all__ = [
    "SOFTParser",
]
