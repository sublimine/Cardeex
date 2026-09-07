"""Exploratory two-group population estimates from reviewed resolved captures.

Independent Cardeex implementation from published mathematics, no database/network.
Chapman estimate and variance: https://pmc.ncbi.nlm.nih.gov/articles/PMC9643167/
The approximate normal interval and small-overlap undercoverage are discussed in
https://link.springer.com/article/10.1007/s10260-026-00840-5 (checked 2026-09-07).

This module does not infer independence from different hostnames. Exactly two
families must be explicitly declared independent with current evidence references.
The caller, not this arithmetic module, verifies those references and resolves
entity identity. Failure of any input qualification returns unknown; invalid
structures raise ValueError. No national pooling or certification is provided.
"""

import math
import re

from .model import CLASSES, KINDS, SOURCE_TYPES, instant, timestamp


ESTIMATOR_VERSION = "1.0.0"
MAX_RECORDS = 100000
MAX_SOURCES_PER_GROUP = 1000
GROUP_FIELDS = frozenset({"id", "sources", "independent", "evidence_ref", "observed_at", "expires_at", "depends_on"})
# Deliberately restrictive policy for this approximation, not a theorem that
# normal intervals achieve nominal coverage for every such population.
MIN_CELL_SUPPORT = 20
REFERENCES = ["https://pmc.ncbi.nlm.nih.gov/articles/PMC9643167/",
              "https://link.springer.com/article/10.1007/s10260-026-00840-5"]
ASSUMPTIONS = ["closed_population_in_the_declared_window", "independent_capture_mechanisms_declared_by_operator",
               "homogeneous_capture_probability_within_each_group", "correct_stable_entity_linkage",
               "same_population_and_unit_in_both_groups"]
LIMITATIONS = ["exploratory_model_not_coverage_certification", "not_calibrated_on_real_dealers",
               "approximate_interval_may_undercover", "unobserved_dependency_and_heterogeneity_can_bias_estimate",
               "independence_evidence_not_verified_by_arithmetic_module"]


def _text(value, field):
    if not isinstance(value, str) or not value.strip() or len(value) > 512 or any(ord(c) < 32 for c in value):
        raise ValueError(f"{field}: bounded nonempty text required")
    return value


def _time(value, field):
    try:
        return instant(_text(value, field))
    except (TypeError, OverflowError) as exc:
        raise ValueError(f"invalid {field}") from exc


def _stratum(stratum, now):
    fields = {"market_country", "vehicle_class", "source_type", "entity_kind", "universe_version",
              "window_start", "window_end"}
    if not isinstance(stratum, dict) or not fields <= stratum.keys() or len(stratum) > 16:
        raise ValueError("stratum requires country/class/source type/entity kind/version/window")
    for key, value in stratum.items():
        _text(key, "stratum field")
        _text(value, key)
    if (not re.fullmatch(r"[A-Z]{2}", stratum["market_country"])
            or stratum["vehicle_class"] not in CLASSES or stratum["source_type"] not in SOURCE_TYPES
            or stratum["source_type"] == "unknown" or stratum["entity_kind"] not in KINDS
            or stratum["entity_kind"] == "unknown"):
        raise ValueError("stratum has unknown country/class/type/unit")
    start, end = _time(stratum["window_start"], "window_start"), _time(stratum["window_end"], "window_end")
    if not start < end <= now:
        raise ValueError("stratum requires a closed, nonempty observation window")
    return start, end


def _groups(groups, now, reasons):
    if not isinstance(groups, list) or len(groups) > 32:
        raise ValueError("independent_groups must be a bounded list")
    if len(groups) != 2:
        reasons.add("two_independent_groups_required")
    mapping, ids = {}, set()
    for group in groups:
        if not isinstance(group, dict) or group.keys() - GROUP_FIELDS:
            raise ValueError("each group must contain only supported public metadata fields")
        gid = _text(group.get("id"), "group.id")
        if gid in ids:
            reasons.add("duplicate_group_ids")
        ids.add(gid)
        if group.get("independent") is not True:
            reasons.add("independence_not_declared")
        dependencies = group.get("depends_on", [])
        if not isinstance(dependencies, list) or len(dependencies) > 32:
            raise ValueError("depends_on must be a bounded list")
        for dependency in dependencies:
            _text(dependency, "dependency")
        if dependencies:
            reasons.add("dependent_capture_groups")
        if not isinstance(group.get("evidence_ref"), str) or not group["evidence_ref"].strip():
            reasons.add("missing_independence_evidence")
        else:
            _text(group["evidence_ref"], "independence evidence")
        observed = _time(group.get("observed_at"), "group.observed_at")
        expires = _time(group.get("expires_at"), "group.expires_at")
        if not observed < expires or observed > now:
            reasons.add("invalid_independence_window")
        if expires <= now:
            reasons.add("expired_independence_evidence")
        sources = group.get("sources")
        if not isinstance(sources, list) or not 1 <= len(sources) <= MAX_SOURCES_PER_GROUP:
            raise ValueError("group.sources must be a nonempty bounded list")
        for source in sources:
            _text(source, "source_id")
            if source in mapping and mapping[source] != gid:
                reasons.add("shared_source_between_groups")
            mapping[source] = gid
    return mapping, ids


def estimate_stratum(records: list, *, independent_groups: list, now: str, stratum: dict) -> dict:
    """Estimate a single closed stratum, or explain why its denominator is unknown.

    Record fields: entity_id, identity_status='resolved', review_status='accepted',
    source_id, evidence_ref, observed_at, expires_at, stratum (exactly this scope).
    Group fields: id, sources, independent=True, evidence_ref, observed_at,
    expires_at, optional depends_on=[]. Repeated records count once per entity
    per group. Any stale/unresolved/mismatched record blocks extrapolation instead
    of silently selecting a favorable sub-sample. Observed counts only qualified
    identities in this stratum; excluded_records records rejected inputs.
    """
    now_text = timestamp(_text(now, "now"))
    at = instant(now_text)
    start, end = _stratum(stratum, at)
    if not isinstance(records, list) or len(records) > MAX_RECORDS:
        raise ValueError("records must be a bounded list")
    reasons = set()
    mapping, ids = _groups(independent_groups, at, reasons)
    captures = {gid: set() for gid in ids}
    evidence_groups = {}
    excluded = 0
    for row in records:
        if not isinstance(row, dict) or len(row) > 32:
            raise ValueError("capture must be a bounded object")
        faults = set()
        entity = row.get("entity_id")
        if not isinstance(entity, str) or not entity.strip() or row.get("identity_status") != "resolved":
            faults.add("unresolved_identity")
        else:
            _text(entity, "entity_id")
        if row.get("review_status") != "accepted":
            faults.add("unreviewed_capture")
        if row.get("stratum") != stratum:
            faults.add("stratum_mismatch")
        source = _text(row.get("source_id"), "source_id")
        if source not in mapping:
            faults.add("source_group_unknown")
        if not isinstance(row.get("evidence_ref"), str) or not row["evidence_ref"].strip():
            faults.add("missing_capture_evidence")
        else:
            _text(row["evidence_ref"], "capture evidence")
            if source in mapping:
                evidence_groups.setdefault(row["evidence_ref"], set()).add(mapping[source])
        observed = _time(row.get("observed_at"), "capture.observed_at")
        expires = _time(row.get("expires_at"), "capture.expires_at")
        if not start <= observed <= end or not observed < expires:
            faults.add("invalid_capture_window")
        if expires <= at:
            faults.add("expired_capture")
        if faults:
            reasons.update(faults)
            excluded += 1
        else:
            captures[mapping[source]].add(entity)
    if any(len(groups) > 1 for groups in evidence_groups.values()):
        reasons.add("shared_capture_evidence_between_groups")
    observed = len(set().union(*captures.values())) if captures else 0
    result = {"status": "unknown", "method": "chapman_two_groups", "method_version": ESTIMATOR_VERSION,
              "as_of": now_text, "stratum": dict(stratum), "observed": observed,
              "group_counts": {gid: len(captures[gid]) for gid in sorted(ids)}, "overlap": None,
              "estimate": None, "interval": None, "coverage_ratio": None,
              "input_records": len(records), "excluded_records": excluded,
              "assumptions": list(ASSUMPTIONS), "limitations": list(LIMITATIONS),
              "references": list(REFERENCES), "reasons": [],
              "independence_evidence": [{key: list(value) if isinstance(value, list) else value
                                         for key, value in group.items() if key in GROUP_FIELDS}
                                        for group in independent_groups]}
    if len(ids) == 2:
        left, right = [captures[gid] for gid in sorted(ids)]
        overlap = len(left & right)
        result["overlap"] = overlap
        n_left, n_right = len(left), len(right)
        if not left or not right:
            reasons.add("empty_capture_group")
        if overlap == 0:
            reasons.add("no_overlap")
        elif min(overlap, n_left - overlap, n_right - overlap) < MIN_CELL_SUPPORT:
            reasons.add("insufficient_interval_support")
        if not reasons:
            estimate = (n_left + 1) * (n_right + 1) / (overlap + 1) - 1
            variance = ((n_left + 1) * (n_right + 1) * (n_left - overlap) * (n_right - overlap)
                        / ((overlap + 1) ** 2 * (overlap + 2)))
            margin = 1.959963984540054 * math.sqrt(variance)
            result.update(status="estimated", estimate=estimate,
                          interval={"lower": max(float(observed), estimate - margin),
                                    "upper": estimate + margin, "nominal_level": 0.95,
                                    "method": "chapman_normal_approximation"})
    result["reasons"] = sorted(reasons)
    return result
