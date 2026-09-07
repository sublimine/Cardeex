"""Descriptive discovery evidence and work debt; no inventory certification."""

from collections import Counter, defaultdict
from datetime import datetime, timezone
from hashlib import sha256
import json
import math

from .profiles import SOURCE_TYPES


KINDS = ("source", "publisher_account", "professional_seller", "point_of_sale", "unknown")
DECISIONS = ("unreviewed", "accepted", "duplicate_candidate", "rejected", "needs_evidence")
TERMINAL_TASKS = {"done", "cancelled"}


def _utc(value):
    if not isinstance(value, str) or not value.endswith("Z") or "T" not in value:
        raise ValueError("now: expected explicit UTC timestamp ending in Z")
    try:
        result = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError("now: invalid UTC timestamp") from exc
    if result.utcoffset().total_seconds() != 0:
        raise ValueError("now: UTC required")
    return result


def _age(value, now):
    try:
        if type(value) in (int, float):
            if not math.isfinite(value):
                return None
            then = datetime.fromtimestamp(value, timezone.utc)
        else:
            then = _utc(value)
        return max(0.0, (now - then).total_seconds())
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def coverage_report(store, profiles, now):
    """Summarize the store's admitted, retained observations at ``now``.

    The caller supplies a read-consistent store view if writes may run during the
    report. Each observation is counted in its own claimed cells: claims from
    separate evidence never form a new country/class Cartesian product. Exact
    candidate counts use sets proportional to retained distinct memberships.
    """
    clock = _utc(now)
    cells = {}
    for country, profile in sorted(profiles.items()):
        for kind in sorted(profile["vehicle_classes"]):
            for source_type in sorted(SOURCE_TYPES):
                cells[(country, kind, source_type)] = {
                    "ids": set(), "kinds": defaultdict(set), "decisions": defaultdict(set),
                    "groups": set(), "tasks": Counter(), "last_observed_at": None,
                }
    unknown = {key: set() for key in ("country", "vehicle_class", "source_type", "locality", "outside_profile")}
    retained_ids, global_kinds, global_decisions = set(), defaultdict(set), defaultdict(set)
    observed_localities = defaultdict(set)
    ambiguous_locality_evidence = set()
    for row in store.iter_observations(now):
        candidate = row["candidate_id"]
        retained_ids.add(candidate)
        kind = row.get("kind", "unknown")
        decision = row.get("decision_status", "unreviewed")
        global_kinds[kind].add(candidate)
        global_decisions[decision].add(candidate)
        countries, classes = row.get("countries", []), row.get("classes", [])
        source_type = row.get("source_type", "unknown")
        locality = row.get("locality_code")
        locality_country = row.get("locality_country")
        if locality:
            # Even a single market country says nothing about physical location.
            # Older unqualified codes remain visible as ambiguous geography.
            if locality_country:
                observed_localities[(locality_country, locality)].add(candidate)
            else:
                ambiguous_locality_evidence.add(candidate)
        for dimension, missing in (("country", not countries), ("vehicle_class", not classes),
                                   ("source_type", source_type == "unknown"),
                                   ("locality", not locality or not locality_country)):
            if missing:
                unknown[dimension].add(candidate)
        for country in countries:
            if country not in profiles:
                unknown["outside_profile"].add(candidate)
            for vehicle_class in classes:
                key = (country, vehicle_class, source_type)
                if key not in cells:
                    unknown["outside_profile"].add(candidate)
                    continue
                cell = cells[key]
                cell["ids"].add(candidate)
                cell["kinds"][kind].add(candidate)
                cell["decisions"][decision].add(candidate)
                if row.get("evidence_group"):
                    cell["groups"].add(row["evidence_group"])
                observed = row.get("observed_at")
                if observed and (cell["last_observed_at"] is None or _utc(observed) > _utc(cell["last_observed_at"])):
                    cell["last_observed_at"] = observed

    plans = {}
    supplied = {}
    locality_definitions = defaultdict(set)
    for plan in store.list_plans():
        plan_id = plan["plan_id"]
        if plan_id in plans and plans[plan_id]["total_tasks"] != plan["total_tasks"]:
            raise ValueError("plan: inconsistent total for immutable plan identity")
        plans[plan_id] = plan
        for locality in plan.get("supplied_localities", []):
            geographic_key = (locality["country"], locality["code"])
            context_key = (plan_id, *geographic_key)
            definition = json.dumps(locality, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            if context_key in supplied and supplied[context_key] != locality:
                raise ValueError("plan: conflicting locality definitions for immutable plan identity")
            supplied[context_key] = locality
            locality_definitions[geographic_key].add(definition)
    conflicting_codes = {key for key, definitions in locality_definitions.items() if len(definitions) > 1}
    for key in conflicting_codes:
        ambiguous_locality_evidence.update(observed_localities[key])
    evidence_contexts = {key for key in supplied if key[1:] in observed_localities and key[1:] not in conflicting_codes}
    counts = Counter()
    seen_tasks, planned_localities, attempted_localities = set(), set(), set()
    tasks_by_plan, pending_strata = defaultdict(set), defaultdict(list)
    oldest, unknown_age = None, 0
    for task in store.iter_tasks():
        task_key = task["task_key"]
        if task_key in seen_tasks:
            continue
        seen_tasks.add(task_key)
        status = task.get("status", "pending")
        counts[status] += 1
        payload = task.get("payload", {})
        ordinal = payload.get("planner_ordinal")
        plan = plans.get(task["plan_id"])
        if (plan is not None and payload.get("parent_task_key") is None
                and type(ordinal) is int and 0 <= ordinal < plan["total_tasks"]):
            tasks_by_plan[task["plan_id"]].add(ordinal)
        human = task.get("kind") == "query" or task.get("access_mode") in {"manual_review", "permission_required"}
        if human and status not in TERMINAL_TASKS:
            counts["human_required"] += 1
        code, country = payload.get("locality_code"), task["country"]
        if code:
            locality_context = (task["plan_id"], country, code)
            planned_localities.add(locality_context)
            if task.get("attempts", 0) > 0 or status not in {"queued", "pending"}:
                attempted_localities.add(locality_context)
        if status not in TERMINAL_TASKS:
            age = _age(task.get("created_at"), clock)
            if age is None:
                unknown_age += 1
            else:
                oldest = age if oldest is None else max(oldest, age)
            pending_strata[task["stratum"]].append(age)
        source_type = payload.get("source_type", "unknown")
        for kind in task.get("classes", []):
            cell = cells.get((country, kind, source_type))
            if cell is not None:
                cell["tasks"][status] += 1
                if human and status not in TERMINAL_TASKS:
                    cell["tasks"]["human_required"] += 1

    frontier_remaining = sum(max(0, plan["total_tasks"] - len(tasks_by_plan[plan_id])) for plan_id, plan in plans.items())
    version_input = {"profiles": profiles, "plans": sorted(plans)}
    version = sha256(json.dumps(version_input, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    result_cells = []
    for (country, kind, source_type), cell in cells.items():
        result_cells.append({
            "market_country": country, "vehicle_class": kind, "source_type": source_type,
            "universe_version": version, "measured_at": now, "denominator_kind": "open_universe",
            "method": "retained_observation_claims", "coverage_ratio": None,
            "declared_comparable_units": None, "certified_units": 0,
            "candidates": len(cell["ids"]),
            "candidate_kinds": {value: len(cell["kinds"][value]) for value in KINDS},
            "decisions": {value: len(cell["decisions"][value]) for value in DECISIONS},
            "evidence_groups": sorted(cell["groups"]), "last_observed_at": cell["last_observed_at"],
            "admitted_sources": 0, "monitored_sources": 0, "certified_sources": 0,
            "inventory_metrics_available": False, "task_scope": dict(sorted(cell["tasks"].items())),
        })
    supplied_keys = set(supplied)
    return {
        "report_version": "1.0.0", "measured_at": now, "universe_version": version,
        "coverage_ratio": None, "denominator_kind": "open_universe",
        "registered_candidates": store.count_candidates(), "retained_evidence_candidates": len(retained_ids),
        "candidate_kinds": {kind: len(global_kinds[kind]) for kind in KINDS},
        "decisions": {decision: len(global_decisions[decision]) for decision in DECISIONS},
        "unknown_claims": {key: len(value) for key, value in unknown.items()},
        "geography": {"scope": "supplied_sample", "coverage_ratio": None,
                      "supplied_localities": len(supplied),
                      "unplanned_localities": len(supplied_keys - planned_localities),
                      "untouched_localities": len(supplied_keys - attempted_localities),
                      "without_candidate_evidence": len(supplied_keys - evidence_contexts),
                      "locality_count_unit": "plan_country_code",
                      "conflicting_locality_codes": len(conflicting_codes),
                      "ambiguous_locality_evidence_candidates": len(ambiguous_locality_evidence),
                      "countries_without_localities": [country for country in sorted(profiles)
                                                       if not any(key[1] == country for key in supplied)],
                      "localities": [locality | {"plan_id": key[0],
                                                "geography_sha256": plans[key[0]].get("geography_sha256"),
                                                "definition_conflict": key[1:] in conflicting_codes,
                                                "planned": key in planned_localities,
                                                "attempted": key in attempted_localities,
                                                "candidate_evidence": key in evidence_contexts}
                                     for key, locality in sorted(supplied.items())]},
        "work": dict(sorted(counts.items())) | {
            "tasks": len(seen_tasks), "plans": len(plans), "blocked": counts["blocked"],
            "human_required": counts["human_required"], "ungenerated_tasks": frontier_remaining,
            "generation_complete": bool(plans) and frontier_remaining == 0,
            "oldest_pending_age_seconds": oldest, "pending_tasks_unknown_age": unknown_age,
            "pending_strata": [{"stratum": stratum, "tasks": len(ages),
                                "oldest_age_seconds": max((age for age in ages if age is not None), default=None)}
                               for stratum, ages in sorted(pending_strata.items())]},
        "cells": result_cells,
    }
