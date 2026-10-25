"""Context-window aware serving: opaque pagination tokens, bounded payloads, record views."""

from __future__ import annotations

import base64
import hashlib
import json
from typing import TYPE_CHECKING, Any

from PyNCBI.core.formatter import format_table_preview
from PyNCBI.core.serialization import json_dumps
from PyNCBI.core.tables import from_records, iter_records

if TYPE_CHECKING:
    from PyNCBI.metadata.schema import StudyRecord


def _collection_fingerprint(items: list[Any]) -> str:
    sample = json.dumps(
        [len(items), str(items[0])[:64] if items else "", str(items[-1])[:64] if items else ""],
    )
    return hashlib.sha1(sample.encode("utf-8")).hexdigest()[:12]  # noqa: S324 - not security, just a guard


def paginate(
    items: list[Any], *, page_size: int = 50, page_token: str | None = None
) -> tuple[list[Any], str | None, int]:
    """Slice ``items`` into a page; returns ``(page, next_page_token, total)``.

    Tokens are opaque, self-describing and bound to the collection they were minted for, so a
    token reused against different data raises ``ValueError`` instead of returning garbage.
    """
    size = max(1, int(page_size))
    total = len(items)
    fingerprint = _collection_fingerprint(items)
    offset = 0
    if page_token:
        try:
            decoded = json.loads(
                base64.urlsafe_b64decode(page_token.encode("ascii") + b"==").decode("utf-8")
            )
            offset = int(decoded["o"])
            if decoded.get("f") != fingerprint:
                raise ValueError("page_token does not belong to this result set")
        except ValueError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ValueError("Invalid page_token") from exc
    page = items[offset : offset + size]
    next_offset = offset + size
    next_token = None
    if next_offset < total:
        next_token = (
            base64.urlsafe_b64encode(
                json.dumps({"o": next_offset, "f": fingerprint}).encode("utf-8")
            )
            .decode("ascii")
            .rstrip("=")
        )
    return page, next_token, total


def bounded_payload(payload: dict[str, Any], *, max_chars: int = 60_000) -> dict[str, Any]:
    """Shrink list-valued fields until the JSON fits ``max_chars`` (records what was cut)."""
    text = json_dumps(payload)
    if len(text) <= max_chars:
        return payload
    out: dict[str, Any] = dict(payload)
    truncated: dict[str, dict[str, int]] = {}
    list_keys = sorted(
        (k for k, v in out.items() if isinstance(v, list) and v),
        key=lambda k: -len(json_dumps(out[k])),
    )
    for key in list_keys:
        items = out[key]
        lo, hi = 0, len(items)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            trial = dict(out)
            trial[key] = items[:mid]
            trial["_truncated"] = {**truncated, key: {"shown": mid, "total": len(items)}}
            if len(json_dumps(trial)) <= max_chars:
                lo = mid
            else:
                hi = mid - 1
        out[key] = items[:lo]
        truncated[key] = {"shown": lo, "total": len(items)}
        out["_truncated"] = truncated
        if len(json_dumps(out)) <= max_chars:
            break
    return out


def study_markdown(record: StudyRecord, *, max_samples: int = 15) -> str:
    """Render a StudyRecord as Markdown (the single human view used by CLI and MCP)."""
    L = [f"# {record.gse_id}: {record.title}", ""]
    L.append(
        f"- **Organism(s):** {', '.join(record.organisms) or 'n/a'} | **Platform(s):** {', '.join(record.platforms) or 'n/a'} | **Samples:** {record.sample_count}"
        + (
            f" (declared {record.declared_sample_count})"
            if record.declared_sample_count and record.declared_sample_count != record.sample_count
            else ""
        )
    )
    if record.experiment_types:
        L.append(f"- **Type:** {', '.join(record.experiment_types)}")
    link_bits = [
        f"{k}: {', '.join(v) if isinstance(v, list) else v}" for k, v in record.links.items()
    ]
    if link_bits:
        L.append(f"- **Links:** {' | '.join(link_bits)}")
    if record.quality is not None:
        q = record.quality
        L.append(
            f"- **Quality score:** {q.score}/100"
            + (
                f" — {len(q.issues)} issue(s): "
                + "; ".join(f"{i.severity}:{i.code}" for i in q.issues)
                if q.issues
                else " — no issues"
            )
        )
    h = record.harmonization or {}
    if h:
        L.append(
            f"- **Harmonization:** {', '.join(h.get('mapped_fields', [])) or 'none'} mapped ({float(h.get('coverage', 0)):.0%} of values); unmapped keys: {', '.join(h.get('unmapped_keys', [])) or 'none'}"
        )
    L.append(
        f"- **Schema:** {record.to_dict()['schema_version']} | **Retrieved:** {record.retrieved_at}"
    )
    if record.summary:
        L += ["", "## Summary", record.summary]
    if record.overall_design:
        L += ["", "## Design", record.overall_design]
    if record.samples:
        titles = {sample.gsm_id: sample.title for sample in record.samples}
        biosamples = {sample.gsm_id: sample.links.get("biosample", "") for sample in record.samples}
        rows = [
            {
                "gsm_id": row["gsm_id"],
                "title": titles.get(str(row["gsm_id"]), ""),
                "biosample": biosamples.get(str(row["gsm_id"]), ""),
                **row,
            }
            for row in iter_records(record.harmonized_table())
        ]
        L += [
            "",
            "## Samples",
            format_table_preview(
                from_records(rows), mode="markdown", max_rows=max_samples, max_cols=20
            ),
        ]
    if record.files:
        L += ["", f"## Files ({len(record.files)})"]
        for f in record.files[:12]:
            L.append(
                f"- `{f.get('name')}` ({f.get('size_human', 'unknown')}, {f.get('source', '')})"
            )
    return "\n".join(L)
