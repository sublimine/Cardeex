"""Strict, offline loading of researched discovery plans.

Access modes describe a proposed channel, never an admission or a licence.
Country codes use the ISO-3166 alpha-2 shape; adding a researched country file
does not require changing this module or grant product scope automatically.
"""

from datetime import date
from itertools import islice
import json
from pathlib import Path
import re
from string import Formatter
from urllib.parse import urlsplit

from .transport import normalize_url


VEHICLE_CLASSES = frozenset({"car", "lcv", "motorcycle", "motorhome"})
SOURCE_TYPES = frozenset({"marketplace", "dealer_owned", "manufacturer_inventory", "auction", "classifieds", "salvage_complete_vehicles", "authorized_aggregator", "unknown"})
STRATEGY_FAMILIES = frozenset({"official_registry", "geo_directory", "marketplace", "manufacturer_network", "trade_directory", "web_index", "local_search", "social_public"})
ACCESS_MODES = frozenset({"open_data", "public_web", "manual_review", "permission_required"})
DEFAULT_DIRECTORY = Path(__file__).with_name("profiles")
# Configuration bounds, not seller/stock thresholds. No DNS or HTTP occurs here.
MAX_PROFILE_BYTES = 1024 * 1024
MAX_PROFILE_FILES = 256
MAX_TEXT_LENGTH = 8192
MAX_COLLECTION_ITEMS = 4096
MAX_PROFILE_NODES = 20000
MAX_PROFILE_DEPTH = 16
MAX_TOTAL_TEXT_LENGTH = 1024 * 1024


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _bounded_structure(profile):
    """Bound direct in-memory validation as well as file-backed configuration."""
    pending = [(profile, 0)]
    nodes = text_length = 0
    while pending:
        value, depth = pending.pop()
        nodes += 1
        _require(nodes <= MAX_PROFILE_NODES, "profile: node limit exceeded")
        _require(depth <= MAX_PROFILE_DEPTH, "profile: nesting limit exceeded")
        if isinstance(value, str):
            _require(len(value) <= MAX_TEXT_LENGTH, "profile: text limit exceeded")
            text_length += len(value)
            _require(text_length <= MAX_TOTAL_TEXT_LENGTH, "profile: total text limit exceeded")
        elif isinstance(value, (dict, list)):
            _require(len(value) <= MAX_COLLECTION_ITEMS, "profile: collection limit exceeded")
            children = value.values() if isinstance(value, dict) else value
            pending.extend((child, depth + 1) for child in children)
            if isinstance(value, dict):
                pending.extend((key, depth + 1) for key in value)


def _object(value, keys, path):
    _require(isinstance(value, dict), f"{path}: expected object")
    _require(set(value) == set(keys), f"{path}: fields must be {sorted(keys)}")


def _text(value, path):
    _require(isinstance(value, str) and bool(value.strip()), f"{path}: expected nonempty string")
    _require(not any(ord(c) < 32 for c in value), f"{path}: control characters are not permitted")


def _strings(value, path):
    _require(isinstance(value, list) and bool(value), f"{path}: expected nonempty array")
    for item in value:
        _text(item, path)
    _require(len(set(value)) == len(value), f"{path}: duplicate values")


def _integer(value, lower, upper, path):
    _require(type(value) is int and lower <= value <= upper, f"{path}: expected integer in {lower}..{upper}")


def _url(value, path):
    _text(value, path)
    try:
        normalized = normalize_url(value)
        valid = urlsplit(normalized).scheme == "https" and not any(c.isspace() for c in value)
    except ValueError:
        valid = False
    _require(valid, f"{path}: expected public HTTPS URL without credentials or nonstandard ports")


def _identifier(value, path):
    _text(value, path)
    _require(re.fullmatch(r"[a-z][a-z0-9_-]*", value) is not None, f"{path}: expected stable lowercase identifier")


def validate_profile(profile):
    """Validate a complete schema 1.0.0 profile, raising ValueError on defects."""
    _bounded_structure(profile)
    _object(profile, {"schema_version", "country", "name", "languages", "vehicle_classes", "geography", "strategies", "vocabulary", "sources", "blind_spots", "review_days"}, "profile")
    _require(profile["schema_version"] == "1.0.0", "schema_version: unsupported version")
    _text(profile["country"], "country")
    _require(re.fullmatch(r"[A-Z]{2}", profile["country"]) is not None, "country: expected uppercase ISO alpha-2 code")
    _text(profile["name"], "name")
    _strings(profile["languages"], "languages")
    languages = set(profile["languages"])
    _require(all(re.fullmatch(r"[a-z]{2,3}(?:-[A-Z]{2})?", item) for item in languages), "languages: invalid language tag")
    _strings(profile["vehicle_classes"], "vehicle_classes")
    _require(set(profile["vehicle_classes"]) == VEHICLE_CLASSES, "vehicle_classes: all four active classes are required")
    _strings(profile["blind_spots"], "blind_spots")
    _integer(profile["review_days"], 1, 366, "review_days")
    geography = profile["geography"]
    _object(geography, {"code_system", "unit_name", "catalogue_url", "notes"}, "geography")
    for key in ("code_system", "unit_name", "notes"):
        _text(geography[key], f"geography.{key}")
    _url(geography["catalogue_url"], "geography.catalogue_url")

    vocabulary = profile["vocabulary"]
    _object(vocabulary, languages, "vocabulary")
    for language, classes in vocabulary.items():
        _object(classes, VEHICLE_CLASSES, f"vocabulary.{language}")
        for vehicle_class, terms in classes.items():
            _strings(terms, f"vocabulary.{language}.{vehicle_class}")

    _require(isinstance(profile["sources"], list) and bool(profile["sources"]), "sources: expected nonempty array")
    source_ids = set()
    source_urls = set()
    for source in profile["sources"]:
        _object(source, {"id", "url", "title", "checked_at", "access_notes"}, "source")
        _identifier(source["id"], "source.id")
        _require(source["id"] not in source_ids, f"source.id: duplicate {source['id']}")
        source_ids.add(source["id"])
        _url(source["url"], "source.url")
        source_urls.add(source["url"])
        _text(source["title"], "source.title")
        _text(source["access_notes"], "source.access_notes")
        checked = source["checked_at"]
        _require(isinstance(checked, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", checked) is not None, "source.checked_at: expected YYYY-MM-DD")
        try:
            date.fromisoformat(checked)
        except ValueError as exc:
            raise ValueError("source.checked_at: invalid calendar date") from exc
    _require(geography["catalogue_url"] in source_urls, "geography.catalogue_url: missing source citation")

    _require(isinstance(profile["strategies"], list) and bool(profile["strategies"]), "strategies: expected nonempty array")
    strategy_ids = set()
    for strategy in profile["strategies"]:
        _object(strategy, {"id", "family", "evidence_group", "priority", "access_mode", "source_type", "seed_urls", "query_templates", "notes", "source_ids", "vehicle_classes"}, "strategy")
        _identifier(strategy["id"], "strategy.id")
        _require(strategy["id"] not in strategy_ids, f"strategy.id: duplicate {strategy['id']}")
        strategy_ids.add(strategy["id"])
        _identifier(strategy["evidence_group"], "strategy.evidence_group")
        for key, allowed in (("family", STRATEGY_FAMILIES), ("access_mode", ACCESS_MODES), ("source_type", SOURCE_TYPES)):
            _text(strategy[key], f"strategy.{key}")
            _require(strategy[key] in allowed, f"strategy.{key}: unsupported value")
        _integer(strategy["priority"], 0, 100, "strategy.priority")
        _strings(strategy["vehicle_classes"], "strategy.vehicle_classes")
        _require(set(strategy["vehicle_classes"]) <= VEHICLE_CLASSES, "strategy.vehicle_classes: outside active scope")
        _strings(strategy["source_ids"], "strategy.source_ids")
        _require(set(strategy["source_ids"]) <= source_ids, "strategy.source_ids: unknown citation")
        _strings(strategy["seed_urls"], "strategy.seed_urls")
        for url in strategy["seed_urls"]:
            _url(url, "strategy.seed_urls")
        _text(strategy["notes"], "strategy.notes")
        templates = strategy["query_templates"]
        _require(isinstance(templates, dict) and bool(templates), "strategy.query_templates: expected nonempty object")
        _require(set(templates) <= languages, "strategy.query_templates: undeclared language")
        for language, queries in templates.items():
            _strings(queries, f"strategy.query_templates.{language}")
            for query in queries:
                try:
                    parts = list(Formatter().parse(query))
                except ValueError as exc:
                    raise ValueError("strategy.query_templates: malformed template") from exc
                fields = []
                for _, field, spec, conversion in parts:
                    if field is not None:
                        _require(field in {"locality", "vehicle_term"} and not spec and conversion is None, "strategy.query_templates: only plain locality and vehicle_term substitutions allowed")
                        fields.append(field)
                _require("locality" in fields, "strategy.query_templates: locality is required")


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, f"JSON: duplicate key {key}")
        result[key] = value
    return result


def load_profile(country, directory=None):
    """Load one fresh profile; the optional directory replaces bundled profiles."""
    _require(isinstance(country, str) and re.fullmatch(r"[A-Za-z]{2}", country) is not None, "country: expected ISO alpha-2 code")
    country = country.upper()
    path = Path(directory) if directory is not None else DEFAULT_DIRECTORY
    try:
        with (path / f"{country}.json").open("rb") as stream:
            payload = stream.read(MAX_PROFILE_BYTES + 1)
        _require(len(payload) <= MAX_PROFILE_BYTES, f"Profile {country}: byte limit exceeded")
        profile = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_pairs)
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError(f"Cannot load profile {country}: {exc}") from exc
    validate_profile(profile)
    _require(profile["country"] == country, f"Profile {country}: filename and country disagree")
    return profile


def load_profiles(directory=None):
    """Load every country JSON in deterministic order, including future countries."""
    path = Path(directory) if directory is not None else DEFAULT_DIRECTORY
    files = list(islice(path.glob("*.json"), MAX_PROFILE_FILES + 1))
    _require(len(files) <= MAX_PROFILE_FILES, "Profile directory: file limit exceeded")
    files.sort()
    _require(bool(files), f"No country profiles found in {path}")
    result = {}
    for file in files:
        _require(re.fullmatch(r"[A-Z]{2}", file.stem) is not None, f"Invalid country profile filename: {file.name}")
        profile = load_profile(file.stem, path)
        result[profile["country"]] = profile
    return result
