"""Bounded, provenance-bearing geography imports and conservative name resolution.

No downloads, catalogue certification, fuzzy matching or numeric code conversion.
The caller supplies original bytes and their recorded checksum; matching that hash
proves byte integrity, not publisher authority or territorial completeness.

CSV field_map maps canonical names to source columns, e.g. INSEE COG 2026:
{code: COM, name: LIBELLE, region: REG, parent_code: COMPARENT, level: TYPECOM}.
level_map can explicitly map COM to municipality and COMA/COMD/ARM to settlement.
Extra source columns are ignored ONLY with an explicit field_map. No spreadsheet
execution, inferred widths or removal of accents is performed.
"""

import copy
import csv
from datetime import datetime, timezone
from hashlib import sha256
import io
import json
import math
import re
import unicodedata

from .model import timestamp
from .transport import normalize_url


CATALOGUE_VERSION = "1.0.0"
MAX_BODY_BYTES = 16 * 1024 * 1024
MAX_SERIALIZED_BYTES = 32 * 1024 * 1024
MAX_JSON_NODES = 2_000_000
MAX_JSON_DEPTH = 16
MAX_UNITS = 100000
MAX_ALIASES = 32
MAX_PARENT_DEPTH = 64
UNIT_FIELDS = frozenset({"code", "name", "region", "rural", "aliases", "parent_code", "level"})
LEVELS = frozenset({"country", "region", "district", "municipality", "settlement"})
PROVENANCE_FIELDS = frozenset({"country", "version", "source_url", "observed_at", "body_sha256"})
IMPORT_FIELDS = frozenset({"field_map", "delimiter", "level_map"})
CATALOGUE_FIELDS = PROVENANCE_FIELDS | frozenset({
    "catalogue_schema_version", "format", "import_options", "units", "ambiguities",
    "completeness", "normalized_sha256"})

# Primary documentation checked 2026-09-07. These are references, not automatic
# downloads, publisher allowlists, permission grants or completeness evidence.
PRIMARY_GEOGRAPHY_REFERENCES = {
    "ES": "https://www.ine.es/dyngs/INEbase/operacion.htm?c=Estadistica_C&cid=1254736177031&idp=1254734710990",
    "FR": "https://www.insee.fr/fr/information/8740222",
    "DE": "https://www.destatis.de/DE/Themen/Laender-Regionen/Regionales/Gemeindeverzeichnis/_inhalt.html",
    "NL": "https://www.cbs.nl/nl-nl/onze-diensten/methoden/classificaties/overig/gemeentelijke-indelingen-per-jaar/indeling-per-jaar/gemeentelijke-indeling-op-1-januari-2026",
    "BE": "https://statbel.fgov.be/fr/open-data/code-refnis",
    "CH": "https://www.agvchapp.bfs.admin.ch/de/communes/query",
}


def _text(value, label, limit=512):
    if (not isinstance(value, str) or not value.strip() or len(value) > limit
            or any(unicodedata.category(c).startswith("C") for c in value)):
        raise ValueError(f"{label}: bounded text without control characters required")
    return value


def _code(value, label):
    _text(value, label, 64)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}", value):
        raise ValueError(f"{label}: code must be an explicit source string")
    return value


def _name_key(value):
    # NFC/case/space normalization keeps accents, punctuation and word order.
    return " ".join(unicodedata.normalize("NFC", value).casefold().split())


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError("non-finite JSON value")


def _check_json_bounds(value):
    """Walk iteratively before serializing: count keys and values, not objects only."""
    stack = [(iter((value,)), 0)]
    nodes = 0
    while stack:
        iterator, depth = stack[-1]
        try:
            item = next(iterator)
        except StopIteration:
            stack.pop()
            continue
        nodes += 1
        if nodes > MAX_JSON_NODES:
            raise ValueError("catalogue JSON node limit exceeded")
        if depth > MAX_JSON_DEPTH:
            raise ValueError("catalogue JSON depth limit exceeded")
        if type(item) is dict:
            nodes += len(item)
            if nodes + len(item) > MAX_JSON_NODES:
                raise ValueError("catalogue JSON node limit exceeded")
            for key in item:
                if type(key) is not str or len(key) > 128:
                    raise ValueError("catalogue JSON keys must be bounded strings")
            stack.append((iter(item.values()), depth + 1))
        elif type(item) is list:
            if nodes + len(item) > MAX_JSON_NODES:
                raise ValueError("catalogue JSON node limit exceeded")
            stack.append((iter(item), depth + 1))
        elif type(item) is str:
            if len(item) > 4096:
                raise ValueError("catalogue JSON string limit exceeded")
        elif type(item) is float:
            if not math.isfinite(item):
                raise ValueError("non-finite JSON value")
        elif type(item) is int:
            if item.bit_length() > 64:
                raise ValueError("catalogue JSON integer limit exceeded")
        elif item is not None and type(item) is not bool:
            raise ValueError("catalogue requires JSON values only")


def _canonical_bytes(value):
    _check_json_bounds(value)
    encoder = json.JSONEncoder(ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    output = io.BytesIO()
    size = 0
    try:
        for chunk in encoder.iterencode(value):
            encoded = chunk.encode("utf-8")
            size += len(encoded)
            if size > MAX_SERIALIZED_BYTES:
                raise ValueError("catalogue serialized bytes limit exceeded")
            output.write(encoded)
    except (UnicodeError, RecursionError) as exc:
        raise ValueError("invalid catalogue JSON encoding") from exc
    return output.getvalue()


def _json(text):
    try:
        result = json.loads(text, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("invalid or too deeply nested JSON") from exc
    _check_json_bounds(result)
    return result


def _metadata(metadata):
    required = PROVENANCE_FIELDS
    allowed = required | IMPORT_FIELDS
    if not isinstance(metadata, dict) or not required <= metadata.keys() or metadata.keys() - allowed:
        raise ValueError("catalogue metadata: country, version, source_url, observed_at and body_sha256 required")
    if not isinstance(metadata["country"], str) or not re.fullmatch(r"[A-Z]{2}", metadata["country"]):
        raise ValueError("country must be explicit ISO-shaped uppercase code")
    _text(metadata["version"], "version", 128)
    _text(metadata["source_url"], "source_url", 4096)
    try:
        source_url = normalize_url(metadata["source_url"])
        if not source_url.startswith("https://"):
            raise ValueError("catalogue source_url requires HTTPS")
        observed = timestamp(_text(metadata["observed_at"], "observed_at", 64))
        if datetime.fromisoformat(observed.replace("Z", "+00:00")) > datetime.now(timezone.utc):
            raise ValueError("observed_at cannot be in the future")
    except (TypeError, OverflowError) as exc:
        raise ValueError("invalid catalogue provenance") from exc
    checksum = metadata["body_sha256"]
    if not isinstance(checksum, str) or not re.fullmatch(r"[a-f0-9]{64}", checksum):
        raise ValueError("body_sha256 must be a lowercase SHA-256 checksum")
    mapping = metadata.get("field_map")
    if mapping is not None:
        if (not isinstance(mapping, dict) or not {"code", "name"} <= mapping.keys()
                or mapping.keys() - UNIT_FIELDS):
            raise ValueError("field_map must map distinct source columns to canonical fields")
        for column in mapping.values():
            _text(column, "field_map column", 128)
        if len(set(mapping.values())) != len(mapping):
            raise ValueError("field_map must map distinct source columns to canonical fields")
    levels = metadata.get("level_map", {})
    if not isinstance(levels, dict) or len(levels) > 32:
        raise ValueError("level_map must be a bounded object")
    for source, target in levels.items():
        _text(source, "source level", 64)
        if not isinstance(target, str) or target not in LEVELS:
            raise ValueError("level_map has unsupported canonical level")
    delimiter = metadata.get("delimiter", ",")
    if not isinstance(delimiter, str) or delimiter not in {",", ";", "\t", "|"}:
        raise ValueError("unsupported CSV delimiter")
    return {key: metadata[key] for key in required} | {"observed_at": observed, "source_url": source_url}


def _csv_rows(text, metadata):
    reader = csv.DictReader(io.StringIO(text, newline=""), delimiter=metadata.get("delimiter", ","), strict=True)
    headers = reader.fieldnames
    if not headers or len(headers) > 256 or len(set(headers)) != len(headers):
        raise ValueError("CSV headers missing or duplicated")
    for header in headers:
        _text(header, "CSV header", 128)
    mapping = metadata.get("field_map")
    if mapping is None:
        if set(headers) - UNIT_FIELDS:
            raise ValueError("unknown CSV columns require explicit field_map")
        mapping = {header: header for header in headers}
    if not {"code", "name"} <= mapping.keys() or not set(mapping.values()) <= set(headers):
        raise ValueError("CSV mapped columns missing")
    for ordinal, row in enumerate(reader):
        if ordinal >= MAX_UNITS:
            raise ValueError("catalogue exceeds unit limit")
        if None in row or any(value is None for value in row.values()):
            raise ValueError("CSV row width differs from headers")
        selected = {field: row[column] for field, column in mapping.items()
                    if row[column] != "" or field in {"code", "name"}}
        if "aliases" in selected:
            selected["aliases"] = _json(selected["aliases"])
        if "rural" in selected:
            if selected["rural"] not in {"true", "false"}:
                raise ValueError("CSV rural must be true or false when known")
            selected["rural"] = selected["rural"] == "true"
        yield selected


def _unit(row, level_map):
    if (not isinstance(row, dict) or not {"code", "name"} <= row.keys()
            or row.keys() - UNIT_FIELDS):
        raise ValueError("unit requires code/name and supported fields")
    result = {"code": _code(row["code"], "unit.code"), "name": _text(row["name"], "unit.name")}
    for field in ("region", "parent_code"):
        if field in row:
            result[field] = _code(row[field], field) if field == "parent_code" else _text(row[field], field)
    level = row.get("level", "municipality")
    if not isinstance(level, str):
        raise ValueError("unit.level must be text")
    level = level_map.get(level, level)
    if level not in LEVELS:
        raise ValueError("unsupported unit.level")
    result["level"] = level
    if "rural" in row:
        if type(row["rural"]) is not bool:
            raise ValueError("rural must be boolean when known")
        result["rural"] = row["rural"]
    aliases = row.get("aliases", [])
    if not isinstance(aliases, list) or len(aliases) > MAX_ALIASES:
        raise ValueError("aliases must be a bounded list")
    result["aliases"] = sorted({_text(alias, "alias") for alias in aliases})
    return result


def _validate_parents(units):
    by_code = {row["code"]: row for row in units}
    for unit in units:
        visited = set()
        code = unit["code"]
        while code is not None:
            if code not in by_code:
                raise ValueError("parent_code missing from catalogue")
            if code in visited:
                raise ValueError("cyclic parent relation")
            if len(visited) >= MAX_PARENT_DEPTH:
                raise ValueError("parent relation exceeds depth limit")
            visited.add(code)
            code = by_code[code].get("parent_code")


def _catalogue(provenance, format, import_options, units):
    """Rebuild all derived fields from validated, normalized units."""
    if len({unit["code"] for unit in units}) != len(units):
        raise ValueError("duplicate unit codes")
    units.sort(key=lambda row: row["code"])
    _validate_parents(units)
    names = {}
    for row in units:
        for name in [row["name"]] + row["aliases"]:
            names.setdefault(_name_key(name), set()).add(row["code"])
    ambiguities = [{"name_key": key, "codes": sorted(codes)} for key, codes in sorted(names.items())
                   if len(codes) > 1]
    return {"catalogue_schema_version": CATALOGUE_VERSION, **provenance, "format": format,
            "import_options": copy.deepcopy(import_options), "units": units,
            "ambiguities": ambiguities,
            "completeness": {"status": "unknown", "coverage_ratio": None,
                             "reason": "independent_catalogue_validation_required"}}


def load_catalogue(body: bytes, metadata: dict, format="json") -> dict:
    """Validate original bytes and return a serializable, sorted catalogue.

    Codes are opaque publisher strings: ES leading zeroes, FR Corsica letters,
    DE AGS, NL GM prefixes, BE NIS and CH BFS forms are never coerced. Shape
    validation cannot establish that a supplied code is nationally authoritative.
    Names shared by different codes are preserved and reported as ambiguities.
    """
    provenance = _metadata(metadata)
    if not isinstance(body, bytes) or len(body) > MAX_BODY_BYTES:
        raise ValueError("catalogue body must be bounded bytes")
    if sha256(body).hexdigest() != provenance["body_sha256"]:
        raise ValueError("catalogue checksum mismatch")
    try:
        text = body.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("catalogue must be UTF-8") from exc
    if format == "json":
        if "field_map" in metadata:
            raise ValueError("field_map applies only to CSV")
        raw = _json(text)
        if isinstance(raw, dict):
            if set(raw) != {"units"}:
                raise ValueError("JSON catalogue object must contain units only")
            raw = raw["units"]
        if not isinstance(raw, list) or len(raw) > MAX_UNITS:
            raise ValueError("units must be a bounded list")
    elif format == "csv":
        try:
            raw = list(_csv_rows(text, metadata))
        except csv.Error as exc:
            raise ValueError("invalid or oversized CSV field") from exc
    else:
        raise ValueError("catalogue format must be json or csv")
    units = [_unit(row, metadata.get("level_map", {})) for row in raw]
    result = _catalogue(provenance, format,
                        {key: metadata[key] for key in IMPORT_FIELDS if key in metadata}, units)
    result["normalized_sha256"] = sha256(_canonical_bytes(result)).hexdigest()
    return result


def validate_catalogue(catalogue: dict) -> dict:
    """Revalidate an imported JSON catalogue; return an independent canonical copy.

    normalized_sha256 hashes the complete catalogue except that field, encoded as
    UTF-8 JSON with ensure_ascii=False, sort_keys=True, separators=(",", ":") and
    allow_nan=False. This detects accidental normalized-data changes only: anyone
    can recompute it. It neither authenticates the publisher nor rechecks the
    declared body_sha256 against unavailable original bytes. Completeness remains
    unknown. Old artifacts without this checksum must be reimported from raw.
    """
    # Bound the entire input, including unsupported fields, before recursive work.
    encoded = _canonical_bytes(catalogue)
    if type(catalogue) is not dict or catalogue.keys() != CATALOGUE_FIELDS:
        raise ValueError("serialized catalogue fields must match the canonical schema exactly")
    if catalogue["catalogue_schema_version"] != CATALOGUE_VERSION:
        raise ValueError("unsupported catalogue schema version")
    checksum = catalogue["normalized_sha256"]
    if not isinstance(checksum, str) or not re.fullmatch(r"[a-f0-9]{64}", checksum):
        raise ValueError("normalized_sha256 must be a lowercase SHA-256 checksum")
    material = {key: value for key, value in catalogue.items() if key != "normalized_sha256"}
    if sha256(_canonical_bytes(material)).hexdigest() != checksum:
        raise ValueError("normalized catalogue checksum mismatch")
    options = catalogue["import_options"]
    if type(options) is not dict or options.keys() - IMPORT_FIELDS:
        raise ValueError("unsupported catalogue import_options")
    provenance = _metadata({key: catalogue[key] for key in PROVENANCE_FIELDS} | options)
    format = catalogue["format"]
    if format not in ("json", "csv"):
        raise ValueError("catalogue format must be json or csv")
    if format == "json" and "field_map" in options:
        raise ValueError("field_map applies only to CSV")
    raw = catalogue["units"]
    if type(raw) is not list or len(raw) > MAX_UNITS:
        raise ValueError("units must be a bounded list")
    # Imported levels are already canonical: reapplying level_map changes meaning.
    units = [_unit(row, {}) for row in raw]
    result = _catalogue(provenance, format, options, units)
    result["normalized_sha256"] = sha256(_canonical_bytes(result)).hexdigest()
    if _canonical_bytes(result) != encoded:
        raise ValueError("catalogue fields or derived values are not canonical")
    return result


def catalogue_localities(catalogue: dict) -> list:
    """Project municipality/settlement units onto the existing planner contract."""
    catalogue = validate_catalogue(catalogue)
    return [{"country": catalogue["country"], **{key: row[key] for key in ("code", "name", "region", "rural")
                                               if key in row}}
            for row in catalogue["units"] if row["level"] in {"municipality", "settlement"}]


def resolve_place(catalogue: dict, name: str, region=None) -> dict:
    """Resolve supplied exact names/aliases only; ambiguity never picks a winner."""
    catalogue = validate_catalogue(catalogue)
    key = _name_key(_text(name, "place name"))
    region_key = _name_key(_text(region, "region")) if region is not None else None
    matches = []
    for row in catalogue["units"]:
        if region_key is not None and _name_key(row.get("region", "")) != region_key:
            continue
        method = "exact_name" if _name_key(row["name"]) == key else "exact_alias"
        if method == "exact_name" or any(_name_key(alias) == key for alias in row["aliases"]):
            matches.append((row["code"], method))
    status = "unknown" if not matches else "matched" if len(matches) == 1 else "ambiguous"
    return {"status": status, "code": matches[0][0] if len(matches) == 1 else None,
            "query_original": {"name": name, "region": region},
            "query_normalized": {"name": key, "region": region_key},
            "normalization_rule": "unicode_nfc_casefold_collapse_whitespace_v1",
            "candidates": sorted(code for code, _ in matches),
            "method": matches[0][1] if len(matches) == 1 else "exact_names_and_aliases",
            "provenance": {key: catalogue[key] for key in
                           ("country", "version", "source_url", "observed_at", "body_sha256",
                            "catalogue_schema_version", "normalized_sha256")}}
