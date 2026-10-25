---
name: Bug report
about: Report a reproducible Python API, CLI or MCP problem
title: ''
labels: ''
assignees: ''
---

**What happened, and what did you expect?**

**Minimal reproduction**
Include the Python snippet, CLI command or MCP tool name and arguments. Use a public
NCBI accession when possible, and state the selected source and output format.

**Environment**
- PyNCBI version (`pyncbi --version`):
- Python version and operating system:
- Interface: Python / CLI / MCP (client and version):

**Error and context**
Include the complete error code/message and relevant output. For connectivity/configuration
issues, `pyncbi status --offline --json` can help. Remove credentials, private data and
personal paths from logs before posting. Explain whether the problem repeats with caching
disabled and whether NCBI's website currently serves the same accession.
