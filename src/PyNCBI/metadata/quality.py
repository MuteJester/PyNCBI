"""Data-quality assessment for study records (completeness score + typed issues)."""

from __future__ import annotations

from collections import Counter

from PyNCBI.metadata.schema import QualityIssue, QualityReport, StudyRecord, now_iso

# (code, severity, penalty)
_PENALTIES: dict[str, tuple[str, int]] = {
    "no_samples": ("error", 40),
    "missing_title": ("error", 15),
    "missing_organism": ("error", 15),
    "missing_platform": ("warning", 10),
    "no_characteristics": ("warning", 15),
    "ragged_characteristics": ("warning", 8),
    "sample_count_mismatch": ("warning", 10),
    "no_files": ("warning", 10),
    "mixed_platforms": ("info", 3),
    "mixed_organisms": ("info", 3),
    "missing_summary": ("info", 2),
    "low_harmonization_coverage": ("info", 5),
    "harmonization_conflicts": ("info", 2),
    "superseries": ("info", 0),
}


def _issue(
    code: str, message: str, *, count: int | None = None, field: str | None = None
) -> QualityIssue:
    severity, _ = _PENALTIES[code]
    return QualityIssue(code=code, severity=severity, message=message, count=count, field=field)


def assess_study(record: StudyRecord, *, ragged_threshold: float = 0.5) -> QualityReport:
    """Score a study record 0-100 and list its quality issues."""
    issues: list[QualityIssue] = []
    n = len(record.samples)

    if n == 0:
        issues.append(_issue("no_samples", "The series lists no samples"))
    if not record.title.strip():
        issues.append(_issue("missing_title", "Series has no title"))
    if not record.summary.strip():
        issues.append(_issue("missing_summary", "Series has no summary"))
    if not record.organisms and not any(s.organism for s in record.samples):
        issues.append(_issue("missing_organism", "No organism on the series or its samples"))
    if not record.platforms and not any(s.platform_id for s in record.samples):
        issues.append(_issue("missing_platform", "No platform (GPL) on the series or its samples"))
    if len(set(record.platforms)) > 1:
        issues.append(
            _issue(
                "mixed_platforms",
                f"Series spans {len(set(record.platforms))} platforms",
                count=len(set(record.platforms)),
            )
        )
    if len({o for o in record.organisms if o}) > 1:
        issues.append(
            _issue(
                "mixed_organisms",
                "Series spans multiple organisms",
                count=len(set(record.organisms)),
            )
        )
    if record.declared_sample_count and n and record.declared_sample_count != n:
        issues.append(
            _issue(
                "sample_count_mismatch",
                f"Series declares {record.declared_sample_count} samples but {n} were retrieved",
                count=abs(record.declared_sample_count - n),
            )
        )
    if not record.files:
        issues.append(_issue("no_files", "No downloadable files were found on the series record"))
    if record.subseries_ids:
        issues.append(
            _issue(
                "superseries",
                f"SuperSeries of {len(record.subseries_ids)} SubSeries; data files live in the SubSeries",
                count=len(record.subseries_ids),
            )
        )

    key_counts: Counter[str] = Counter()
    samples_with_chars = 0
    for s in record.samples:
        if s.characteristics:
            samples_with_chars += 1
        for k in s.characteristics:
            key_counts[k] += 1
    if n and samples_with_chars == 0:
        issues.append(_issue("no_characteristics", "No sample characteristics on any sample"))
    elif n >= 3:
        ragged = [k for k, c in key_counts.items() if c / n < ragged_threshold]
        if ragged:
            issues.append(
                _issue(
                    "ragged_characteristics",
                    f"{len(ragged)} characteristic key(s) present on fewer than {int(ragged_threshold * 100)}% of samples: {', '.join(sorted(ragged)[:8])}",
                    count=len(ragged),
                )
            )

    cov = record.harmonization.get("coverage") if record.harmonization else None
    if cov is not None and samples_with_chars and float(cov) < 0.5:
        issues.append(
            _issue(
                "low_harmonization_coverage",
                f"Only {float(cov):.0%} of characteristic values map to the controlled vocabulary",
                field="harmonization",
            )
        )
    if record.harmonization.get("conflicts"):
        issues.append(
            _issue(
                "harmonization_conflicts",
                f"{record.harmonization['conflicts']} conflicting characteristic value(s) across synonymous keys",
                count=int(record.harmonization["conflicts"]),
            )
        )

    score = 100
    for issue in issues:
        score -= _PENALTIES[issue.code][1]
    score = max(0, min(100, score))

    completeness = {
        "title": 1.0 if record.title.strip() else 0.0,
        "summary": 1.0 if record.summary.strip() else 0.0,
        "organism": (sum(1 for s in record.samples if s.organism) / n)
        if n
        else (1.0 if record.organisms else 0.0),
        "platform": (sum(1 for s in record.samples if s.platform_id) / n)
        if n
        else (1.0 if record.platforms else 0.0),
        "characteristics": (samples_with_chars / n) if n else 0.0,
        "files": 1.0 if record.files else 0.0,
    }
    return QualityReport(
        score=score,
        issues=issues,
        completeness={k: round(v, 4) for k, v in completeness.items()},
        assessed_at=now_iso(),
    )
