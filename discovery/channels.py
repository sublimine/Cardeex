"""Source-specific discovery contracts. This module never performs network I/O.

Independently designed from primary documentation checked on 2026-09-07 (URLs
below). Availability, an assessment reference, and documentation are not access
permission. The engine must validate admission on every request and publication.

``complete`` describes the bounded source query/file, never dealer or inventory
coverage. SearXNG cannot establish an exhaustive result set and returns None.
Continuations remain available when the caller's page budget is exhausted so the
engine can persist debt. Sirene accepts a bounded, locally supplied UTF-8 CSV
slice with its header and original snapshot date; national ZIP/Parquet ingestion
is deliberately outside this parser's memory and transport contract.
"""
from __future__ import annotations

import csv
import io
import json
import math
import re
from datetime import date, datetime
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from .transport import normalize_url

CHANNEL_VERSION = "discovery-channels/1"
DOCUMENTATION_CHECKED_AT = "2026-09-07"
PRIMARY_DOCUMENTATION = {
    "searxng_json": ["https://docs.searxng.org/dev/search_api.html"],
    "overpass_json": [
        "https://dev.overpass-api.de/output_formats.html",
        "https://dev.overpass-api.de/overpass-doc/en/full_data/bbox.html",
        "https://wiki.openstreetmap.org/wiki/Overpass_API/Overpass_QL",
        "https://wiki.openstreetmap.org/wiki/Tag:shop=car",
        "https://wiki.openstreetmap.org/wiki/Tag:shop=motorcycle",
    ],
    "rdw_socrata": [
        "https://opendata.rdw.nl/api/views/5k74-3jha.json",
        "https://www.rdw.nl/over-rdw/dienstverlening/open-data/algemene-informatie",
        "https://dev.socrata.com/docs/paging.html",
        "https://dev.socrata.com/docs/queries/where.html",
    ],
    "sirene_csv": [
        "https://www.insee.fr/fr/information/3591226",
        "https://www.data.gouv.fr/datasets/base-sirene-des-entreprises-et-de-leurs-etablissements-siren-siret",
        "https://www.data.gouv.fr/api/1/datasets/r/65946639-3150-4492-a988-944e2633531e",
    ],
}
MAX_DOCUMENT_BYTES = 2 * 1024 * 1024
MAX_NODES = 20000
MAX_DEPTH = 24
MAX_ROWS = 2000
MAX_TEXT = 4096
RDW_ENDPOINT = "https://opendata.rdw.nl/resource/5k74-3jha.json"
_RDW_FIELDS = "volgnummer,naam_bedrijf,gevelnaam,straat,huisnummer,huisnummer_toevoeging,postcode_numeriek,postcode_alfanumeriek,plaats"
_CLASSES = {"car", "lcv", "motorcycle", "motorhome"}
_FIELDS = {"adapter", "endpoint", "assessment_ref", "countries", "vehicle_classes", "parameters", "max_pages"}
_OSM_TAGS = {("shop", "car"), ("shop", "motorcycle"), ("shop", "caravan"),
             ("shop", "car_repair"), ("shop", "motorcycle_repair"), ("craft", "car_repair")}
_PARAMETERS = {
    "searxng_json": {"language", "categories"},
    "overpass_json": {"bbox", "tags", "row_limit", "timeout_seconds"},
    "rdw_socrata": {"page_size"},
    "sirene_csv": {"snapshot_date", "activity_codes", "nomenclature"},
}


class ChannelError(ValueError):
    """Invalid binding or source-format evidence; messages contain no raw data."""


class ChannelLimitError(ChannelError):
    """A parser/request bound prevents asserting complete results."""


def _text(value, name, maximum=MAX_TEXT, allow_empty=False):
    if not isinstance(value, str) or len(value) > maximum:
        raise ChannelError(f"{name}: invalid text")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ChannelError(f"{name}: control characters are forbidden")
    value = value.strip()
    if not allow_empty and not value:
        raise ChannelError(f"{name}: nonempty text required")
    return value


def _integer(value, name, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ChannelError(f"{name}: integer outside the supported range")
    return value


def _cursor(value, default, minimum=0):
    if value is None:
        return default
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,18}", value):
        raise ChannelError("Invalid channel continuation")
    result = int(value)
    if result < minimum or result > 999999999999999999:
        raise ChannelError("Invalid channel continuation")
    return result


def _url(value):
    try:
        return normalize_url(value)
    except (ValueError, UnicodeError, TypeError):
        raise ChannelError("Unsafe or invalid channel URL") from None


def _date(value):
    value = _text(value, "snapshot_date", 10)
    try:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError
        date.fromisoformat(value)
    except ValueError:
        raise ChannelError("Invalid source snapshot date") from None
    return value


def _timestamp(value):
    value = _text(value, "source_timestamp", 40)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})?", value):
        raise ChannelError("Invalid source timestamp")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ChannelError("Invalid source timestamp") from None
    return value  # Preserve unknown timezone; never invent a UTC interpretation.


def _bbox(value):
    if not isinstance(value, list) or len(value) != 4 or any(
        type(number) not in (int, float) or (type(number) is float and not math.isfinite(number)) for number in value
    ):
        raise ChannelError("bbox: four finite coordinates required")
    south, west, north, east = value
    if not (-90 <= south < north <= 90 and -180 <= west < east <= 180):
        raise ChannelError("bbox: invalid geographic bounds")
    # A deliberately small task footprint avoids accidental national queries.
    # Larger regions must be partitioned by the planner, not silently clipped.
    if north - south > 2 or east - west > 2:
        raise ChannelLimitError("bbox: partition into cells of at most two degrees per side")
    return list(value)


def validate_binding(binding: dict) -> dict:
    """Return a strict canonical binding; this validates shape, not authorization."""
    if not isinstance(binding, dict) or set(binding) != _FIELDS:
        raise ChannelError("An explicit complete channel binding is required")
    adapter = binding["adapter"]
    if not isinstance(adapter, str) or adapter not in _PARAMETERS:
        raise ChannelError("Unsupported channel adapter")
    endpoint = _url(binding["endpoint"])
    parts = urlsplit(endpoint)
    if parts.scheme != "https" or parts.query or parts.fragment:
        raise ChannelError("Channel endpoint must be explicit HTTPS without query or fragment")
    if adapter == "rdw_socrata" and endpoint != RDW_ENDPOINT:
        raise ChannelError("RDW adapter requires its documented recognised-company dataset")
    result = dict(adapter=adapter, endpoint=endpoint,
                  assessment_ref=_text(binding["assessment_ref"], "assessment_ref", 256),
                  max_pages=_integer(binding["max_pages"], "max_pages", 1, 1000))
    for key in ("countries", "vehicle_classes"):
        values = binding[key]
        if not isinstance(values, list) or not values or len(values) > 250:
            raise ChannelError(f"{key}: an explicit nonempty scope is required")
        if any(not isinstance(v, str) for v in values):
            raise ChannelError(f"{key}: invalid scope values")
        if key == "countries" and any(not re.fullmatch(r"[A-Z]{2}", v) for v in values):
            raise ChannelError("countries: invalid country code")
        if key == "vehicle_classes" and not set(values) <= _CLASSES:
            raise ChannelError("vehicle_classes: unsupported active class")
        result[key] = sorted(set(values))
    supplied = binding["parameters"]
    if not isinstance(supplied, dict) or set(supplied) - _PARAMETERS[adapter]:
        raise ChannelError("Unsupported channel parameters")
    p = dict(supplied)
    if adapter == "searxng_json":
        for key in p:
            p[key] = _text(p[key], key, 128)
            if not re.fullmatch(r"[A-Za-z0-9_,:-]+", p[key]):
                raise ChannelError("Invalid search language or category")
    elif adapter == "overpass_json":
        if "bbox" in p:
            p["bbox"] = _bbox(p["bbox"])
        tags = p.get("tags", [{"key": "shop", "value": "car"}, {"key": "shop", "value": "motorcycle"},
                              {"key": "shop", "value": "caravan"}, {"key": "shop", "value": "car_repair"}])
        if not isinstance(tags, list) or not 1 <= len(tags) <= len(_OSM_TAGS):
            raise ChannelError("tags: unsupported tag selection")
        canonical = set()
        for tag in tags:
            if not isinstance(tag, dict) or set(tag) != {"key", "value"} or not all(isinstance(v, str) for v in tag.values()):
                raise ChannelError("tags: key and value required")
            pair = (tag["key"], tag["value"])
            if pair not in _OSM_TAGS:
                raise ChannelError("tags: unsupported discovery tag")
            canonical.add(pair)
        p["tags"] = [{"key": key, "value": value} for key, value in sorted(canonical)]
        p["row_limit"] = _integer(p.get("row_limit", 500), "row_limit", 1, MAX_ROWS)
        p["timeout_seconds"] = _integer(p.get("timeout_seconds", 25), "timeout_seconds", 1, 60)
    elif adapter == "rdw_socrata":
        p["page_size"] = _integer(p.get("page_size", 100), "page_size", 1, MAX_ROWS)
    else:
        if set(p) != _PARAMETERS[adapter]:
            raise ChannelError("Sirene requires snapshot_date, activity_codes and nomenclature")
        p["snapshot_date"] = _date(p["snapshot_date"])
        if p["nomenclature"] not in ("NAFRev2", "NAF2025"):
            raise ChannelError("Unsupported Sirene activity nomenclature")
        codes = p["activity_codes"]
        if not isinstance(codes, list) or not 1 <= len(codes) <= 100 or any(
            not isinstance(code, str) or not re.fullmatch(r"\d{2}\.\d{2}[A-Z]", code) for code in codes
        ):
            raise ChannelError("Sirene requires explicit reviewed activity codes")
        p["activity_codes"] = sorted(set(codes))
    result["parameters"] = p
    return result


def request_url(binding: dict, task: dict, cursor=None) -> str:
    """Build a GET URL for SafeFetcher. No headers, credentials or network calls."""
    b = validate_binding(binding)
    if not isinstance(task, dict):
        raise ChannelError("Channel task must be an object")
    adapter, p = b["adapter"], b["parameters"]
    if adapter == "sirene_csv":
        raise ChannelError("Sirene CSV is a local-import channel; network download is not implemented")
    if adapter == "searxng_json":
        query = task.get("query")
        if query is None and task.get("kind") == "query":
            query = task.get("locator")
        parameters = {"q": _text(query, "query", 2048), "format": "json", "pageno": _cursor(cursor, 1, 1), **p}
    elif adapter == "overpass_json":
        if cursor is not None:
            raise ChannelError("Overpass queries are partitioned geographically, not paginated")
        bounds = _bbox(p.get("bbox"))
        if "bbox" in task and task["bbox"] != bounds:
            raise ChannelError("Task cannot override the assessed geographic partition")
        area = ",".join(str(v) for v in bounds)
        statements = "".join(f'nwr["{tag["key"]}"="{tag["value"]}"]({area});' for tag in p["tags"])
        parameters = {"data": f'[out:json][timeout:{p["timeout_seconds"]}];({statements});out center {p["row_limit"]};'}
    else:
        last = _cursor(cursor, 0)
        parameters = {"$select": _RDW_FIELDS,
                      "$order": "volgnummer ASC", "$limit": p["page_size"], "$where": f"volgnummer > {last}"}
    return _url(b["endpoint"] + "?" + urlencode(parameters))


def _json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ChannelError("Duplicate JSON keys are ambiguous")
            result[key] = value
        return result

    def constant(_value):
        raise ChannelError("Non-finite JSON numbers are unsupported")

    try:
        value = json.loads(text, object_pairs_hook=pairs, parse_constant=constant)
    except (json.JSONDecodeError, RecursionError, UnicodeError):
        raise ChannelError("Malformed or excessively nested channel JSON") from None
    stack, count = [(value, 0)], 0
    while stack:
        item, depth = stack.pop()
        count += 1
        if count > MAX_NODES or depth > MAX_DEPTH:
            raise ChannelLimitError("Channel JSON node/depth budget exceeded")
        if isinstance(item, dict):
            stack.extend((v, depth + 1) for v in item.values())
        elif isinstance(item, list):
            stack.extend((v, depth + 1) for v in item)
        elif isinstance(item, str) and len(item) > MAX_TEXT:
            raise ChannelLimitError("Channel JSON field exceeds the text budget")
        elif isinstance(item, float) and not math.isfinite(item):
            raise ChannelError("Non-finite JSON numbers are unsupported")
    return value


def _list(value, name):
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise ChannelError(f"{name}: expected source row objects")
    if len(value) > MAX_ROWS:
        raise ChannelLimitError("Source row budget exceeded")
    return value


def _optional(row, field):
    value = row.get(field)
    return _text(value, "source field", allow_empty=True) if value is not None else ""


def _row(locator, label, signals, selector, adapter, kind="unknown", fetch_allowed=False):
    return {"locator": _url(locator), "label": _text(label, "label", allow_empty=True),
            "kind": kind, "relation": "organization" if signals.get("source_record_id") else "outbound_link",
            "signals": {**signals, "claim_status": "unverified", "assertion_method": adapter,
                        "parser_version": CHANNEL_VERSION, "evidence_selector": selector,
                        "discovery_fetch_allowed": fetch_allowed}}


def _result(rows, continuation=None, complete=None, warnings=()):
    return dict(rows=rows, continuation=continuation, complete=complete, warnings=sorted(set(warnings)))


def _origin(binding, origin):
    origin = _url(origin)
    p, expected = urlsplit(origin), urlsplit(binding["endpoint"])
    if p.scheme != expected.scheme or p.netloc != expected.netloc or p.path != expected.path or p.fragment:
        raise ChannelError("Response origin does not match the assessed channel endpoint")
    try:
        params = parse_qs(p.query, keep_blank_values=True, max_num_fields=20)
    except ValueError:
        raise ChannelError("Invalid source request parameters") from None
    if any(len(values) != 1 for values in params.values()):
        raise ChannelError("Ambiguous source request parameters")
    return origin, {key: values[0] for key, values in params.items()}


def _search(binding, data, parameters):
    if not isinstance(data, dict) or "error" in data:
        raise ChannelError("Search response is not a valid successful result envelope")
    items = _list(data.get("results"), "Search results")
    page = _cursor(parameters.get("pageno"), 1, 1)
    warnings, rows, seen = [], [], set()
    if "unresponsive_engines" in data and not isinstance(data["unresponsive_engines"], list):
        raise ChannelError("Search upstream-error metadata changed format")
    if data.get("unresponsive_engines"):
        warnings.append("upstream_search_errors")
    for index, item in enumerate(items):
        locator = item.get("url")
        try:
            locator = _url(locator)
        except ChannelError:
            warnings.append("unsafe_or_invalid_locator")
            continue
        if locator in seen:
            warnings.append("duplicate_source_locator")
            continue
        seen.add(locator)
        # Rank, snippets, query country and query class are intentionally omitted.
        rows.append(_row(locator, _optional(item, "title"), {"locator_role": "search_result"},
                         f"$.results[{index}]", binding["adapter"], fetch_allowed=True))
    if page >= binding["max_pages"] and items:
        warnings.append("page_budget_reached")
    if not items:
        warnings.append("empty_search_page_is_not_absence")
    continuation = str(page + 1) if items else None
    if continuation is not None and len(continuation) > 18:
        continuation = None
        warnings.append("cursor_range_exhausted")
    return _result(rows, continuation, None, warnings)


def _overpass(binding, data):
    if not isinstance(data, dict) or "error" in data:
        raise ChannelError("Invalid Overpass result envelope")
    if "version" in data and (type(data["version"]) not in (int, float) or data["version"] != 0.6):
        raise ChannelError("Unsupported Overpass format version")
    if "remark" in data and not isinstance(data["remark"], str):
        raise ChannelError("Overpass diagnostic metadata changed format")
    items = _list(data.get("elements"), "Overpass elements")
    warnings, rows, seen = [], [], set()
    timestamp = None
    if "osm3s" in data:
        if not isinstance(data["osm3s"], dict):
            raise ChannelError("Invalid Overpass metadata")
        if "timestamp_osm_base" in data["osm3s"]:
            timestamp = _timestamp(data["osm3s"]["timestamp_osm_base"])
    if not timestamp:
        warnings.append("source_timestamp_unknown")
    if data.get("remark"):
        warnings.append("overpass_remark")
    limit = binding["parameters"]["row_limit"]
    if len(items) >= limit:
        warnings.append("row_limit_reached")
    if len(items) > limit:
        raise ChannelLimitError("Overpass exceeded the requested result limit")
    for index, item in enumerate(items):
        typ, identity = item.get("type"), item.get("id")
        if typ not in ("node", "way", "relation") or type(identity) is not int or identity <= 0:
            raise ChannelError("Invalid Overpass element identity")
        source_id = f"{typ}/{identity}"
        if source_id in seen:
            raise ChannelError("Duplicate Overpass element identity")
        seen.add(source_id)
        tags = item.get("tags", {})
        if not isinstance(tags, dict):
            raise ChannelError("Invalid Overpass tags")
        retained = {key: _optional(tags, key) for key in (
            "name", "shop", "craft", "amenity", "motorcycle:sales", "addr:street", "addr:housenumber",
            "addr:city", "addr:postcode", "addr:country") if key in tags}
        locator = "https://www.openstreetmap.org/" + source_id
        record = locator
        fetch_allowed = False
        website = tags.get("website") or tags.get("contact:website")
        if website:
            try:
                locator = _url(website)
                fetch_allowed = True
            except ChannelError:
                warnings.append("unsafe_or_invalid_locator")
        signals = {"source_record_id": source_id, "source_record_locator": record, "osm_tags": retained,
                   "locator_role": "published_website" if fetch_allowed else "registry_record",
                   "website_status": "published" if fetch_allowed else "rejected" if website else "not_published"}
        if timestamp:
            signals["source_timestamp"] = timestamp
        coords = item if typ == "node" else item.get("center", {})
        if not isinstance(coords, dict):
            raise ChannelError("Invalid Overpass coordinates")
        if "lat" in coords or "lon" in coords:
            lat, lon = coords.get("lat"), coords.get("lon")
            if any(type(v) not in (int, float) or (type(v) is float and not math.isfinite(v)) for v in (lat, lon)) or not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise ChannelError("Invalid Overpass coordinates")
            signals["published_coordinates"] = {"latitude": lat, "longitude": lon,
                "precision": "mapped_node" if typ == "node" else "bounding_box_center"}
        kind = "professional_seller" if tags.get("shop") == "car" or (
            tags.get("shop") == "motorcycle" and tags.get("motorcycle:sales") == "yes") else "unknown"
        rows.append(_row(locator, retained.get("name", ""), signals, f"$.elements[{index}]",
                         binding["adapter"], kind, fetch_allowed))
    complete = "overpass_remark" not in warnings and "row_limit_reached" not in warnings
    return _result(rows, complete=complete, warnings=warnings)


def _rdw(binding, data, parameters):
    items = _list(data, "RDW dataset")
    size = binding["parameters"]["page_size"]
    if set(parameters) - {"$select", "$where", "$order", "$limit"}:
        raise ChannelError("Unexpected RDW request parameters")
    for key, expected in (("$select", _RDW_FIELDS), ("$order", "volgnummer ASC"), ("$limit", str(size))):
        if key in parameters and parameters[key] != expected:
            raise ChannelError("RDW request differs from the assessed binding")
    if len(items) > size:
        raise ChannelLimitError("RDW exceeded the requested page size")
    last = 0
    if "$where" in parameters:
        match = re.fullmatch(r"volgnummer > ([0-9]{1,18})", parameters["$where"])
        if not match:
            raise ChannelError("Unrecognized RDW cursor predicate")
        last = int(match.group(1))
    rows = []
    for index, item in enumerate(items):
        identity = _cursor(item.get("volgnummer"), None, 1)
        if identity is None or identity <= last:
            raise ChannelError("Missing, repeated or unordered RDW source identifier")
        last = identity
        label = _optional(item, "gevelnaam") or _optional(item, "naam_bedrijf")
        if not label:
            raise ChannelError("RDW company name missing; dataset schema may have changed")
        street = " ".join(filter(None, (_optional(item, key) for key in ("straat", "huisnummer", "huisnummer_toevoeging"))))
        postcode = _optional(item, "postcode_numeriek") + _optional(item, "postcode_alfanumeriek")
        address = {key: value for key, value in {"streetAddress": street, "postalCode": postcode,
                   "addressLocality": _optional(item, "plaats")}.items() if value}
        signals = {"source_record_id": str(identity), "locator_role": "registry_record",
                   "recognition_does_not_establish_sales": True}
        if address:
            signals["published_address"] = address
        # The fragment denotes a retained source record, not a claimed dealer URL.
        locator = binding["endpoint"] + "#rdw=" + str(identity)
        rows.append(_row(locator, label, signals, f"$[{index}]", binding["adapter"]))
    verified = set(parameters) == {"$select", "$where", "$order", "$limit"}
    warnings = ["live_dataset_not_snapshot", "source_timestamp_unknown"]
    if not verified:
        warnings.append("request_parameters_unverified")
    return _result(rows, str(last) if verified and len(items) == size else None,
                   len(items) < size if verified else None, warnings)


def _sirene(binding, text):
    p = binding["parameters"]
    required = {"siret", "statutDiffusionEtablissement", "etatAdministratifEtablissement",
                "activitePrincipaleEtablissement", "nomenclatureActivitePrincipaleEtablissement"}
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    rows, warnings, seen = [], [], set()
    try:
        header = next(reader)
        if len(header) != len(set(header)) or not required <= set(header) or len(header) > 200:
            raise ChannelError("Invalid or changed Sirene CSV header")
        for index, values in enumerate(reader, 2):
            if index > MAX_ROWS + 1:
                raise ChannelLimitError("Sirene slice row budget exceeded")
            if len(values) != len(header) or any(len(v) > MAX_TEXT for v in values):
                raise ChannelError("Malformed or oversized Sirene CSV row")
            item = dict(zip(header, values))
            diffusion = item["statutDiffusionEtablissement"]
            if diffusion != "O":
                if diffusion not in ("P", "N"):
                    raise ChannelError("Unknown Sirene diffusion status")
                warnings.append("restricted_diffusion_omitted")
                continue
            if item["nomenclatureActivitePrincipaleEtablissement"] != p["nomenclature"]:
                raise ChannelError("Sirene activity nomenclature changed; review filters")
            if item["activitePrincipaleEtablissement"] not in p["activity_codes"]:
                warnings.append("outside_activity_scope_omitted")
                continue
            identity = item["siret"]
            if not re.fullmatch(r"[0-9]{14}", identity) or identity in seen:
                raise ChannelError("Invalid or duplicate Sirene establishment identifier")
            seen.add(identity)
            status = item["etatAdministratifEtablissement"]
            if status not in ("A", "F"):
                raise ChannelError("Unknown Sirene administrative status")
            signals = {"source_record_id": identity, "locator_role": "registry_record", "observed_status": status,
                       "source_snapshot_date": p["snapshot_date"], "activity_code": item["activitePrincipaleEtablissement"],
                       "activity_nomenclature": p["nomenclature"], "activity_is_not_inventory": True}
            for source, target in (("codeCommuneEtablissement", "published_locality_code"),
                                   ("libelleCommuneEtablissement", "published_locality_name")):
                if item.get(source):
                    signals[target] = _optional(item, source)
            if item.get("dateDernierTraitementEtablissement"):
                signals["source_timestamp"] = _timestamp(item["dateDernierTraitementEtablissement"])
            label = _optional(item, "denominationUsuelleEtablissement") or _optional(item, "enseigne1Etablissement")
            rows.append(_row(binding["endpoint"] + "#siret=" + identity, label, signals,
                             f"csv:row[{index}]", binding["adapter"]))
    except (csv.Error, StopIteration):
        raise ChannelError("Malformed or empty Sirene CSV") from None
    return _result(rows, complete=True, warnings=warnings + ["local_slice_not_national_census"])


def parse_response(binding: dict, body: bytes, content_type: str, origin: str) -> dict:
    """Parse retained bytes to claims and explicit continuation/debt metadata.

    No raw response errors, contact fields, personal identity fields or snippets
    are returned. Caller stores its own observed_at/checksum/policy provenance.
    """
    b = validate_binding(binding)
    _source, parameters = _origin(b, origin)
    if not isinstance(body, bytes) or not body.strip():
        raise ChannelError("Nonempty retained response bytes are required")
    if len(body) > MAX_DOCUMENT_BYTES:
        raise ChannelLimitError("Channel response byte budget exceeded")
    if not isinstance(content_type, str):
        raise ChannelError("Response content type is required")
    mime = content_type.split(";", 1)[0].strip().lower()
    accepted = {"text/csv", "application/csv"} if b["adapter"] == "sirene_csv" else {"application/json"}
    if mime not in accepted:
        raise ChannelError("Unexpected channel response content type")
    charset = re.search(r"charset\s*=\s*[\"']?([\w-]+)", content_type, re.I)
    if charset and charset.group(1).lower() not in ("utf-8", "utf8", "utf-8-sig"):
        raise ChannelError("Only UTF-8 source responses are supported")
    try:
        text = body.decode("utf-8-sig")
    except UnicodeError:
        raise ChannelError("Invalid UTF-8 source response") from None
    if "\x00" in text:
        raise ChannelError("Binary source response is unsupported")
    if b["adapter"] == "sirene_csv":
        return _sirene(b, text)
    data = _json(text)
    if b["adapter"] == "searxng_json":
        return _search(b, data, parameters)
    if b["adapter"] == "overpass_json":
        # A smaller limit or different bbox in the actual URL changes the
        # meaning of completion. Unqualified retained files remain unknown.
        if parameters:
            expected = parse_qs(urlsplit(request_url(b, {})).query)
            if parameters != {key: values[0] for key, values in expected.items()}:
                raise ChannelError("Overpass request differs from the assessed binding")
        result = _overpass(b, data)
        if not parameters:
            result["complete"] = None
            result["warnings"].append("request_parameters_unverified")
        return result
    return _rdw(b, data, parameters)
