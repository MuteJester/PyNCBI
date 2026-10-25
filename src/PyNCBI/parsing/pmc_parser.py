"""PubMed Central (JATS) full-text XML parser for PyNCBI.

Turns `efetch.fcgi?db=pmc&retmode=xml` output into a `PMCFullText` with flattened,
classified sections (`methods`, `results`, `data_availability`, ...) so agents can read a
study's exact wet-lab and bioinformatics protocol without scraping HTML.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from PyNCBI.core.exceptions import ParseError
from PyNCBI.core.models import PMCFullText, PMCSection

_KIND_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "data_availability",
        re.compile(
            r"(data|code|material|resource|software)s?\s+(and\s+\w+\s+)?availability|availability\s+of\s+(data|code|supporting)|accession\s+(codes?|numbers?)|data\s+(access|sharing|deposition)|availability\s+statement",
            re.I,
        ),
    ),
    (
        "methods",
        re.compile(
            r"\bmethods?\b|experimental\s+procedures?|materials?\s+and|study\s+design|patients?\s+and\s+methods|star\s*★?\s*methods|methodology|protocol",
            re.I,
        ),
    ),
    ("results", re.compile(r"\bresults?\b|\bfindings\b", re.I)),
    ("discussion", re.compile(r"\bdiscussion\b", re.I)),
    ("conclusion", re.compile(r"\bconclusions?\b|concluding\s+remarks|\bsummary\b", re.I)),
    ("introduction", re.compile(r"\bintroduction\b|\bbackground\b", re.I)),
    (
        "supplementary",
        re.compile(r"supplement(al|ary)|supporting\s+information|additional\s+files?", re.I),
    ),
    (
        "acknowledgments",
        re.compile(
            r"acknowledg|funding|author\s+contributions|competing\s+interests|conflicts?\s+of\s+interest|declaration",
            re.I,
        ),
    ),
    ("abstract", re.compile(r"^abstract$", re.I)),
)

_SEC_TYPE_KINDS = {
    "materials|methods": "methods",
    "methods": "methods",
    "materials": "methods",
    "results": "results",
    "discussion": "discussion",
    "intro": "introduction",
    "introduction": "introduction",
    "conclusions": "conclusion",
    "data-availability": "data_availability",
    "supplementary-material": "supplementary",
}

# Elements whose text is not prose and should be skipped when flattening a section
_SKIP_TAGS = {
    "table-wrap",
    "table",
    "fig",
    "graphic",
    "media",
    "disp-formula",
    "tex-math",
    "mml:math",
    "xref",
    "label",
    "sup",
    "ref-list",
    "fn-group",
    "supplementary-material",
    "object-id",
    "alt-text",
    "inline-graphic",
    "ext-link-ignored",
}

_WS_RE = re.compile(r"[ \t\r\f\v]+")
_SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([.,;:)\]])")


def classify_section_title(title: str, sec_type: str | None = None) -> str:
    """Classify a section heading into a coarse kind."""
    if sec_type:
        mapped = _SEC_TYPE_KINDS.get(sec_type.strip().lower())
        if mapped:
            return mapped
    clean = (title or "").strip()
    if not clean:
        return "other"
    for kind, pattern in _KIND_PATTERNS:
        if pattern.search(clean):
            return kind
    return "other"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _element_text(elem: ET.Element) -> str:
    """Flatten an element's prose text, skipping tables/figures/xrefs, normalizing spaces."""
    parts: list[str] = []

    def _walk(node: ET.Element) -> None:
        if _local(node.tag) in _SKIP_TAGS:
            if node.tail:
                parts.append(node.tail)
            return
        if node.text:
            parts.append(node.text)
        for child in node:
            _walk(child)
        if node.tail:
            parts.append(node.tail)

    if elem.text:
        parts.append(elem.text)
    for child in elem:
        _walk(child)
    text = "".join(parts)
    text = _WS_RE.sub(" ", text)
    text = _SPACE_BEFORE_PUNCT_RE.sub(r"\1", text)
    return text.strip()


def _paragraphs_of(sec: ET.Element) -> str:
    """Prose paragraphs directly under a <sec> (not those of nested <sec> children)."""
    blocks: list[str] = []
    for child in sec:
        tag = _local(child.tag)
        if tag in ("title", "sec"):
            continue
        if tag in ("p", "list", "list-item", "disp-quote", "boxed-text", "statement", "notes"):
            txt = _element_text(child)
            if txt:
                blocks.append(txt)
    return "\n\n".join(blocks)


def _walk_sections(
    parent: ET.Element,
    level: int,
    inherited_kind: str | None,
    path_prefix: str,
    out: list[PMCSection],
) -> None:
    for sec in parent:
        if _local(sec.tag) != "sec":
            continue
        title_el = sec.find("title")
        title = _element_text(title_el) if title_el is not None else ""
        kind = classify_section_title(title, sec.get("sec-type"))
        if inherited_kind and (
            kind == "other"
            or (
                inherited_kind in ("methods", "data_availability")
                and kind not in ("data_availability",)
            )
        ):
            kind = inherited_kind
        path = f"{path_prefix} > {title}" if path_prefix and title else (title or path_prefix)
        out.append(
            PMCSection(title=title, kind=kind, level=level, text=_paragraphs_of(sec), path=path)
        )
        _walk_sections(sec, level + 1, kind, path, out)


def parse_pmc_article_xml(xml_text: str) -> PMCFullText:
    """Parse a single-article PMC `efetch` XML payload into a `PMCFullText`."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ParseError("PMC XML", str(exc)) from exc

    article = root if _local(root.tag) == "article" else root.find(".//article")
    if article is None:
        err = root.find(".//error")
        detail = _element_text(err) if err is not None else "no <article> element in response"
        return PMCFullText(pmcid="", notes=f"PMC returned no article: {detail}")

    pmid = pmcid = doi = ""
    for aid in article.findall("./front/article-meta/article-id"):
        id_type = aid.get("pub-id-type", "")
        val = (aid.text or "").strip()
        if id_type == "pmid":
            pmid = val
        elif id_type in ("pmc", "pmcid"):
            pmcid = val if val.upper().startswith("PMC") else f"PMC{val}"
        elif id_type == "doi":
            doi = val

    title_el = article.find("./front/article-meta/title-group/article-title")
    title = _element_text(title_el) if title_el is not None else ""
    journal_el = article.find("./front/journal-meta//journal-title")
    journal = _element_text(journal_el) if journal_el is not None else ""
    year = ""
    for pd_el in article.findall("./front/article-meta/pub-date"):
        y = pd_el.findtext("year")
        if y:
            year = y.strip()
            if pd_el.get("pub-type") in ("epub", "ppub"):
                break
    abstract_el = article.find("./front/article-meta/abstract")
    abstract = _element_text(abstract_el) if abstract_el is not None else ""

    sections: list[PMCSection] = []
    body = article.find("./body")
    if body is not None:
        # Some articles have top-level <p> before any <sec>
        loose = _paragraphs_of(body)
        if loose:
            # Untitled prose before the first <sec> is the introduction in most layouts
            sections.append(PMCSection(title="", kind="introduction", level=1, text=loose, path=""))
        _walk_sections(body, 1, None, "", sections)

    back = article.find("./back")
    if back is not None:
        _walk_sections(back, 1, None, "", sections)
        for tag, kind in (("ack", "acknowledgments"), ("notes", "other"), ("fn-group", "other")):
            for el in back.findall(f"./{tag}"):
                t_el = el.find("title")
                t = _element_text(t_el) if t_el is not None else tag
                k = classify_section_title(t) if tag != "ack" else kind
                txt = _paragraphs_of(el) or _element_text(el)
                if txt:
                    sections.append(PMCSection(title=t, kind=k, level=1, text=txt, path=t))

    is_oa = body is not None and any(s.text for s in sections)
    notes = ""
    if not is_oa:
        notes = (
            "Full text is not available from PMC in XML form (the publisher restricts "
            "redistribution or the article is not in the PMC Open Access subset)."
        )
    return PMCFullText(
        pmcid=pmcid,
        pmid=pmid,
        doi=doi,
        title=title,
        journal=journal,
        pub_year=year,
        abstract=abstract,
        is_open_access=is_oa,
        sections=sections,
        notes=notes,
    )
