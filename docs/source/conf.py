# Configuration file for the Sphinx documentation builder.
#
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

from __future__ import annotations

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../src")))

# -- Project information -----------------------------------------------------

project = "PyNCBI"
author = "Thomas Konstantinovsky"
copyright = f"2022-{datetime.now().year}, {author}"

# The full version, including alpha/beta/rc tags (single source of truth: the package)
from PyNCBI import __version__  # noqa: E402

release = __version__
version = ".".join(__version__.split(".")[:2])

# -- General configuration ---------------------------------------------------

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx.ext.intersphinx",
    "sphinx_autodoc_typehints",
]

templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]
suppress_warnings = ["ref.python"]

# -- Options for HTML output -------------------------------------------------

html_theme = "sphinx_rtd_theme"
html_static_path: list[str] = []
html_title = f"PyNCBI {release}"

# -- Extension configuration -------------------------------------------------

# Napoleon settings
napoleon_google_docstring = True
napoleon_numpy_docstring = True
napoleon_include_init_with_doc = True
# Render 'Attributes:' sections as field lists so they do not collide with autodoc members
napoleon_use_ivar = True

# Autodoc settings
autodoc_member_order = "bysource"
autodoc_typehints = "description"

# Backward-compatible aliases point at classes documented under their canonical names
autodoc_default_options = {
    "exclude-members": "HttpClient,HttpClientConfig,CompressedPickleCache",
}

# Intersphinx mapping
intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    "pandas": ("https://pandas.pydata.org/docs/", None),
    "numpy": ("https://numpy.org/doc/stable/", None),
}
