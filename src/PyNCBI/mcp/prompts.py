"""MCP prompts: ready-made agent workflows rendered from templates."""

from __future__ import annotations

from typing import Any

MCP_PROMPTS: list[dict[str, Any]] = [
    {
        "name": "find_dataset",
        "title": "Find a usable GEO dataset",
        "description": "Screen GEO for studies on a topic that actually carry the metadata you need, then pick one.",
        "arguments": [
            {
                "name": "topic",
                "description": "What the study should be about, e.g. 'COVID-19 PBMC transcriptome'",
                "required": True,
            },
            {
                "name": "organism",
                "description": "Organism filter, e.g. 'Homo sapiens'",
                "required": False,
            },
            {
                "name": "needs",
                "description": "Comma-separated metadata that must be present, e.g. 'sex,age'",
                "required": False,
            },
        ],
    },
    {
        "name": "study_deep_dive",
        "title": "Understand one GEO series",
        "description": "Get the design, harmonized cohort, data sources and quality of a single study before using it.",
        "arguments": [
            {
                "name": "gse_id",
                "description": "GEO Series accession, e.g. GSE152418",
                "required": True,
            }
        ],
    },
    {
        "name": "cohort_download",
        "title": "Download one cohort safely",
        "description": "Select a sample subset with a filter and fetch only those files, checking the size first.",
        "arguments": [
            {"name": "gse_id", "description": "GEO Series accession", "required": True},
            {
                "name": "filter",
                "description": "Cohort filter, e.g. \"disease == 'Control'\"",
                "required": True,
            },
            {
                "name": "pattern",
                "description": "Filename glob, e.g. '*.idat.gz'",
                "required": False,
            },
        ],
    },
    {
        "name": "paper_methods",
        "title": "Read the study's methods",
        "description": "Pull the published Methods and Data Availability sections behind a dataset.",
        "arguments": [{"name": "pmid", "description": "PubMed ID of the paper", "required": True}],
    },
]


_PROMPT_TEMPLATES: dict[str, str] = {
    "find_dataset": (
        "Find a GEO dataset about: {topic}.\n\n"
        "1. Call `ncbi_screen` with query='{topic}'{organism_clause}{needs_clause} to compare candidates "
        "without downloading anything. Read sample_count, download_human, harmonized_fields and quality_score.\n"
        "2. Reject studies whose metadata coverage is too low for the analysis, and say why.\n"
        "3. For the best candidate, call `ncbi_metadata` to confirm the cohort, then report the accession, "
        "its sample breakdown and the download size before fetching anything."
    ),
    "study_deep_dive": (
        "Give me a complete picture of GEO series {gse_id}.\n\n"
        "1. Call `ncbi_metadata` with gse_id='{gse_id}' for the design, the harmonized cohort (sex, age, "
        "disease, tissue with provenance) and the quality score; call out any quality issues.\n"
        "2. Call `ncbi_inspect` (or read the record's files) to list what is downloadable and how large it is.\n"
        "3. Call `ncbi_expression` with gse_id='{gse_id}' to see which source holds the analysis-ready matrix "
        "and its shape. Do not download raw files yet.\n"
        "4. If a paper is linked, call `ncbi_pubmed` with methods=true and summarise how the data was generated.\n"
        "5. Finish with a short verdict: is this study usable, and for what."
    ),
    "cohort_download": (
        "Download only the {filter} cohort of {gse_id}.\n\n"
        "1. Call `ncbi_metadata` with gse_id='{gse_id}' and where=\"{filter}\" to confirm which samples match.\n"
        "2. Call `ncbi_download` with accession='{gse_id}', where=\"{filter}\"{pattern_clause} and "
        "dry_run=true; report the file count and total size.\n"
        "3. Only if that is reasonable, repeat the call with dry_run=false, then list what landed on disk."
    ),
    "paper_methods": (
        "Explain how the data behind PMID {pmid} was produced.\n\n"
        "1. Call `ncbi_pubmed` with pmids_or_query='{pmid}' and methods=true.\n"
        "2. Summarise the wet-lab protocol, the genome build and aligner, the normalization, and how the "
        "clinical groups were defined. Quote the Data Availability statement and any GEO accessions.\n"
        "3. If the full text is not open access, say so and fall back to the abstract."
    ),
}


def get_prompt(name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """Render a workflow prompt into MCP `messages`."""
    args = {k: str(v) for k, v in (arguments or {}).items() if v is not None}
    spec = next((p for p in MCP_PROMPTS if p["name"] == name), None)
    if spec is None:
        raise ValueError(
            f"Unknown prompt '{name}'. Available: {', '.join(p['name'] for p in MCP_PROMPTS)}"
        )
    missing = [
        a["name"]
        for a in spec.get("arguments", [])
        if a.get("required") and not args.get(a["name"])
    ]
    if missing:
        raise ValueError(f"Prompt '{name}' requires argument(s): {', '.join(missing)}")

    organism = args.get("organism", "")
    needs = args.get("needs", "")
    pattern = args.get("pattern", "")
    text = _PROMPT_TEMPLATES[name].format(
        topic=args.get("topic", ""),
        gse_id=args.get("gse_id", ""),
        filter=args.get("filter", ""),
        pmid=args.get("pmid", ""),
        organism_clause=f", organism='{organism}'" if organism else "",
        needs_clause=f", require={[n.strip() for n in needs.split(',') if n.strip()]}"
        if needs
        else "",
        pattern_clause=f", pattern='{pattern}'" if pattern else "",
    )
    return {
        "description": spec["description"],
        "messages": [{"role": "user", "content": {"type": "text", "text": text}}],
    }
