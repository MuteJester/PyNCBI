"""Citation export (BibTeX / RIS) for PubMed articles — pure stdlib."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PyNCBI.core.models import PubMedArticle

_BIB_ESCAPE = {"&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_"}


def _year(article: PubMedArticle) -> str:
    m = re.search(r"(19|20)\d{2}", article.pub_date or "")
    return m.group(0) if m else ""


def _split_author(author: str) -> tuple[str, str]:
    """'Arunachalam PS' -> ('Arunachalam', 'PS'); 'Doe, Jane' -> ('Doe', 'Jane')."""
    a = author.strip()
    if "," in a:
        last, _, fore = a.partition(",")
        return last.strip(), fore.strip()
    parts = a.split()
    if len(parts) == 1:
        return parts[0], ""
    return " ".join(parts[:-1]), parts[-1]


def _author_pairs(article: PubMedArticle) -> list[tuple[str, str]]:
    """(last, fore) pairs, using PubMed's own LastName/ForeName split when available."""
    if article.author_details:
        return [
            (d.get("last", ""), d.get("fore", "")) for d in article.author_details if d.get("last")
        ]
    return [_split_author(a) for a in article.authors]


def citation_key(article: PubMedArticle) -> str:
    pairs = _author_pairs(article)
    last = pairs[0][0] if pairs else "pmid"
    last = re.sub(r"[^A-Za-z0-9]", "", last) or "pmid"
    return f"{last}{_year(article) or article.pmid}"


def _bib(text: str) -> str:
    out = text
    for ch, rep in _BIB_ESCAPE.items():
        out = out.replace(ch, rep)
    return out


def to_bibtex(article: PubMedArticle, key: str | None = None) -> str:
    """Render a PubMed article as a BibTeX `@article` entry."""
    fields: list[tuple[str, str]] = []
    if article.authors or article.author_details:
        names = _author_pairs(article)
        fields.append(
            (
                "author",
                " and ".join(f"{_bib(last)}, {_bib(fore)}".rstrip(", ") for last, fore in names),
            )
        )
    if article.title:
        fields.append(("title", "{" + _bib(article.title.rstrip(".")) + "}"))
    if article.journal:
        fields.append(("journal", _bib(article.journal)))
    year = _year(article)
    if year:
        fields.append(("year", year))
    if article.doi:
        fields.append(("doi", article.doi))
    if article.pmid:
        fields.append(("pmid", article.pmid))
    if article.pmc_id:
        fields.append(("pmcid", article.pmc_id))
    if article.keywords:
        fields.append(("keywords", _bib(", ".join(article.keywords))))
    if article.doi:
        fields.append(("url", f"https://doi.org/{article.doi}"))
    elif article.pmid:
        fields.append(("url", f"https://pubmed.ncbi.nlm.nih.gov/{article.pmid}/"))
    body = ",\n".join(
        f"  {k} = {{{v}}}" if not (v.startswith("{") and v.endswith("}")) else f"  {k} = {v}"
        for k, v in fields
    )
    return f"@article{{{key or citation_key(article)},\n{body}\n}}\n"


def to_ris(article: PubMedArticle) -> str:
    """Render a PubMed article as an RIS record."""
    lines = ["TY  - JOUR"]
    for last, fore in _author_pairs(article):
        lines.append(f"AU  - {last}, {fore}".rstrip(", "))
    if article.title:
        lines.append(f"TI  - {article.title}")
    if article.journal:
        lines.append(f"JO  - {article.journal}")
    year = _year(article)
    if year:
        lines.append(f"PY  - {year}")
    if article.doi:
        lines.append(f"DO  - {article.doi}")
    if article.pmid:
        lines.append(f"AN  - {article.pmid}")
        lines.append(f"UR  - https://pubmed.ncbi.nlm.nih.gov/{article.pmid}/")
    if article.abstract:
        lines.append(f"AB  - {article.abstract.replace(chr(10), ' ')}")
    for kw in article.keywords:
        lines.append(f"KW  - {kw}")
    lines.append("ER  - ")
    return "\n".join(lines) + "\n"


def export_citations(
    articles: list[PubMedArticle],
    save_to: str | Path | None,
    *,
    fmt: str | None = None,
) -> Path | str:
    """Export articles as BibTeX or RIS; returns the written Path, or the text when `save_to` is None.

    `fmt` defaults from the file suffix ('.bib' -> bibtex, '.ris' -> ris).
    """
    resolved = (fmt or "").strip().lower()
    if not resolved and save_to is not None:
        resolved = "ris" if str(save_to).lower().endswith(".ris") else "bibtex"
    resolved = resolved or "bibtex"
    if resolved in ("bib", "bibtex"):
        used: dict[str, int] = {}
        chunks: list[str] = []
        for art in articles:
            base = citation_key(art)
            n = used.get(base, 0)
            used[base] = n + 1
            key = base if n == 0 else f"{base}{chr(ord('a') + n - 1)}"
            chunks.append(to_bibtex(art, key=key))
        text = "\n".join(chunks)
    elif resolved == "ris":
        text = "\n".join(to_ris(a) for a in articles)
    else:
        raise ValueError("fmt must be 'bibtex' or 'ris'")
    if save_to is None:
        return text
    dest = Path(save_to)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")
    return dest
