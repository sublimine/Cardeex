"""Finite, reproducible discovery proposals; this module performs no network I/O.

Paging bounds materialized tasks, not the declared frontier. Planning keeps
iterators proportional to supplied geography rather than expanding every query
combination. An offset resumes the same immutable plan; it is not a new plan.
"""

from collections import deque
from functools import partial
from hashlib import sha256
from itertools import chain, islice
import json
from string import Formatter

from .profiles import SOURCE_TYPES, validate_profile


PLANNER_VERSION = "1.0.0"
MAX_PAGE_TASKS = 100000


def _digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _text(value, name):
    if not isinstance(value, str) or not value.strip() or any(ord(char) < 32 for char in value):
        raise ValueError(f"{name}: expected nonempty text without control characters")


def _geography(profiles, localities):
    if not isinstance(localities, list):
        raise ValueError("localities: expected an explicit list of supplied places")
    result = {}
    for locality in localities:
        if not isinstance(locality, dict) or not {"country", "code", "name"} <= locality.keys():
            raise ValueError("locality: country, code and name are required")
        if set(locality) - {"country", "code", "name", "region", "rural"}:
            raise ValueError("locality: unsupported fields")
        for key in ("country", "code", "name"):
            _text(locality[key], f"locality.{key}")
        if locality["country"] not in profiles:
            raise ValueError(f"locality.country: no profile for {locality['country']}")
        if "region" in locality:
            _text(locality["region"], "locality.region")
        if "rural" in locality and type(locality["rural"]) is not bool:
            raise ValueError("locality.rural: expected boolean when known")
        key = (locality["country"], locality["code"])
        if key in result and result[key] != locality:
            raise ValueError(f"locality: conflicting definitions for {key}")
        result[key] = dict(locality)
    return [result[key] for key in sorted(result)]


def _round_robin(lanes, offset=0):
    """Seek into ragged round robin using lane lengths and lazy start factories.

    A completed round consumes min(round, lane_length) items from each lane.
    Binary search finds the last complete round; the remaining prefix determines
    which lane goes next. No skipped task is constructed, formatted or hashed.
    """
    if offset == sum(length for length, _ in lanes):
        return
    low, high = 0, max((length for length, _ in lanes), default=0)
    while low < high:
        middle = (low + high + 1) // 2
        if sum(min(middle, length) for length, _ in lanes) <= offset:
            low = middle
        else:
            high = middle - 1
    consumed = [min(low, length) for length, _ in lanes]
    remainder, cursor = offset - sum(consumed), 0
    for index, (length, _) in enumerate(lanes):
        if remainder and consumed[index] < length:
            consumed[index] += 1
            remainder -= 1
            cursor = index + 1
    order = chain(range(cursor, len(lanes)), range(cursor))
    active = deque(iter(lanes[index][1](consumed[index])) for index in order
                   if consumed[index] < lanes[index][0])
    while active:
        iterator = active.popleft()
        try:
            yield next(iterator)
        except StopIteration:
            continue
        active.append(iterator)


def _strategies(profile):
    # Priority breaks no fairness guarantees: every strategy gets a turn.
    return sorted(profile["strategies"], key=lambda item: (-item["priority"], item["id"]))


def _task(plan_id, profile, strategy, kind, locator, classes, locality=None,
          language=None, query_identity=None):
    code = locality["code"] if locality else None
    identity = {"plan_id": plan_id, "country": profile["country"], "strategy": strategy["id"],
                "kind": kind, "locator": locator, "classes": sorted(classes),
                "locality_code": code, "language": language, "query_identity": query_identity}
    return {
        "task_key": "task:" + _digest(identity), "plan_id": plan_id,
        "stratum": profile["country"] + ":" + _digest([code, strategy["id"]]),
        "country": profile["country"], "classes": sorted(classes), "strategy": strategy["id"],
        "kind": kind, "locator": locator, "priority": strategy["priority"],
        "access_mode": strategy["access_mode"],
        "payload": {"evidence_group": strategy["evidence_group"], "source_type": strategy["source_type"],
                    "locality_code": code, "locality_name": locality["name"] if locality else None,
                    "region": locality.get("region") if locality else None,
                    "rural": locality.get("rural") if locality else None,
                    "language": language, "parent_task_key": None,
                    "source_ids": sorted(strategy["source_ids"]),
                    "requires_human_execution": kind == "query",
                    "scope_is_evidence": False},
    }


def _seed_tasks(plan_id, profile, strategy, offset=0):
    for url in islice(sorted(set(strategy["seed_urls"])), offset, None):
        yield _task(plan_id, profile, strategy, "fetch", url, strategy["vehicle_classes"])


def _query_group(plan_id, profile, strategy, locality, language, template_index, template, vehicle_class, offset=0):
    place = locality["name"]
    if locality.get("region"):
        place += ", " + locality["region"]
    place += ", " + profile["country"]
    classes = [vehicle_class] if vehicle_class else strategy["vehicle_classes"]
    terms = profile["vocabulary"][language][vehicle_class] if vehicle_class else [""]
    for term_index in range(offset, len(terms)):
        term = terms[term_index]
        query = template.format(locality=place, vehicle_term=term)
        yield _task(plan_id, profile, strategy, "query", query, classes, locality,
                    language, [template_index, term_index])


def _uses_vehicle_term(template):
    return any(field == "vehicle_term" for _, field, _, _ in Formatter().parse(template))


def _local_queries(plan_id, profile, strategy, locality, offset=0):
    groups = []
    for language, templates in sorted(strategy["query_templates"].items()):
        for index, template in enumerate(templates):
            classes = sorted(strategy["vehicle_classes"]) if _uses_vehicle_term(template) else [None]
            for vehicle_class in classes:
                count = len(profile["vocabulary"][language][vehicle_class]) if vehicle_class else 1
                groups.append((count, partial(_query_group, plan_id, profile, strategy, locality,
                                              language, index, template, vehicle_class)))
    yield from _round_robin(groups, offset)


def _locality_tasks(plan_id, profile, strategies, query_counts, locality, offset=0):
    lanes = [(query_counts[strategy["id"]], partial(_local_queries, plan_id, profile, strategy, locality))
             for strategy in strategies]
    yield from _round_robin(lanes, offset)


def _country_tasks(plan_id, profile, localities, offset=0):
    strategies = _strategies(profile)
    query_counts = {strategy["id"]: _query_count(profile, strategy) for strategy in strategies}
    seeds = [(len(set(strategy["seed_urls"])), partial(_seed_tasks, plan_id, profile, strategy))
             for strategy in strategies]
    lanes = [(sum(length for length, _ in seeds), partial(_round_robin, seeds))]
    locality_count = sum(query_counts.values())
    lanes.extend((locality_count, partial(_locality_tasks, plan_id, profile, strategies, query_counts, locality))
                 for locality in localities)
    yield from _round_robin(lanes, offset)


def _query_count(profile, strategy):
    count = 0
    for language, templates in strategy["query_templates"].items():
        for template in templates:
            count += (sum(len(profile["vocabulary"][language][kind]) for kind in strategy["vehicle_classes"])
                      if _uses_vehicle_term(template) else 1)
    return count


def build_plan(profiles, localities, epoch, max_tasks=10000, offset=0):
    """Return a deterministic page plus the complete frontier's finite manifest.

    All supplied localities are a sample, including an empty list. Profile access
    modes describe proposed channels and do not approve any third-party access.
    Query proposals require human execution; a future search provider needs its
    own adapter and admission. Page limits are 1..100000. Count-aware seeks skip
    completed rounds mathematically, constructing only tasks in the requested
    page. Work and memory depend on input profiles/geography and page size, not
    on the number of preceding tasks in the Cartesian frontier.
    """
    if not isinstance(profiles, dict) or not profiles:
        raise ValueError("profiles: expected at least one country profile")
    for country, value in profiles.items():
        validate_profile(value)
        if country != value["country"]:
            raise ValueError("profiles: key and country disagree")
    _text(epoch, "epoch")
    if type(max_tasks) is not int or not 1 <= max_tasks <= MAX_PAGE_TASKS:
        raise ValueError(f"max_tasks: expected integer in 1..{MAX_PAGE_TASKS}")
    if type(offset) is not int or offset < 0:
        raise ValueError("offset: expected a nonnegative integer")
    places = _geography(profiles, localities)
    grouped = {country: [] for country in sorted(profiles)}
    for place in places:
        grouped[place["country"]].append(place)
    profile_hash, geography_hash = _digest(profiles), _digest(places)
    plan_id = "plan:" + _digest([PLANNER_VERSION, profile_hash, geography_hash, epoch])
    country_counts = {country: sum(len(set(strategy["seed_urls"])) + len(grouped[country]) * _query_count(value, strategy)
                                  for strategy in value["strategies"])
                      for country, value in profiles.items()}
    total = sum(country_counts.values())
    if offset > total:
        raise ValueError("offset: beyond the declared frontier")
    lanes = [(country_counts[country], partial(_country_tasks, plan_id, profiles[country], grouped[country]))
             for country in sorted(profiles)]
    tasks = list(islice(_round_robin(lanes, offset), max_tasks))
    for ordinal, task in enumerate(tasks, start=offset):
        task["payload"]["planner_ordinal"] = ordinal
    next_offset = offset + len(tasks)
    cells = [{"market_country": country, "vehicle_class": kind, "source_type": source_type,
              "strategy_ids": sorted(strategy["id"] for strategy in value["strategies"]
                                     if kind in strategy["vehicle_classes"] and strategy["source_type"] == source_type)}
             for country, value in sorted(profiles.items()) for kind in sorted(value["vehicle_classes"])
             for source_type in sorted(SOURCE_TYPES)]
    return {"plan_id": plan_id, "planner_version": PLANNER_VERSION, "profile_sha256": profile_hash,
            "geography_sha256": geography_hash, "epoch": epoch, "offset": offset,
            "next_offset": next_offset if next_offset < total else None, "total_tasks": total,
            "generation_complete": next_offset == total,
            "missing_geography": [country for country, values in grouped.items() if not values],
            "geography_scope": "supplied_sample", "geography_coverage_ratio": None,
            "supplied_localities": places, "cells": cells, "tasks": tasks}
