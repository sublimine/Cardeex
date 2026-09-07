"""Offline verification of Cardeex specifications and bounded reference models.

This is not the product runtime, a scraper, or a distributed-system benchmark.
It never fetches a schema remotely or writes project data. Run from any cwd.
"""

from __future__ import annotations

import copy
import ast
import base64
import hashlib
import hmac
import itertools
import json
import math
from collections import Counter
from pathlib import Path
import re
import random
import sys
from datetime import datetime
from urllib.parse import unquote

import yaml
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012


ROOT = Path(__file__).resolve().parents[1]
CHECKS: Counter[str] = Counter()


def require(condition: bool, message: str, category: str = "structure") -> None:
    if not condition:
        raise AssertionError(message)
    CHECKS[category] += 1


def unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def read_json(path: str) -> dict:
    return json.loads((ROOT / path).read_text(encoding="utf-8"), object_pairs_hook=unique_object)


class UniqueYamlLoader(yaml.SafeLoader):
    pass


def yaml_mapping(loader: UniqueYamlLoader, node: yaml.MappingNode, deep: bool = False) -> dict:
    pairs = [(loader.construct_object(k, deep=deep), loader.construct_object(v, deep=deep))
             for k, v in node.value]
    return unique_object(pairs)


UniqueYamlLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, yaml_mapping)


def walk(value: object, path: tuple[str, ...] = ()):
    yield path, value
    if isinstance(value, dict):
        for key, child in value.items():
            yield from walk(child, path + (str(key),))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk(child, path + (str(index),))


def pointer(document: object, fragment: str) -> object:
    if not fragment or fragment == "#":
        return document
    require(fragment.startswith("#/"), f"Unsupported non-pointer fragment: {fragment}")
    value = document
    for part in fragment[2:].split("/"):
        key = unquote(part).replace("~1", "/").replace("~0", "~")
        value = value[int(key)] if isinstance(value, list) else value[key]
    return value


def registry_for(schemas: list[dict]) -> Registry:
    registry = Registry()
    for schema in schemas:
        resource = Resource(schema, DRAFT202012)
        registry = registry.with_resource(schema["$id"], resource)
        for candidate in [ROOT / "contracts/control.schema.json", ROOT / "contracts/domain.schema.json"]:
            if json.loads(candidate.read_text(encoding="utf-8"))["$id"] == schema["$id"]:
                registry = registry.with_resource(candidate.as_uri(), resource)
    return registry


def validate_examples(schema: dict, registry: Registry) -> None:
    Draft202012Validator.check_schema(schema)
    require(schema.get("$schema") == "https://json-schema.org/draft/2020-12/schema",
            f"Unpinned dialect in {schema.get('$id')}")
    validator = Draft202012Validator(schema, registry=registry, format_checker=FormatChecker())
    example_count = 0
    for path, node in walk(schema):
        if not isinstance(node, dict) or "examples" not in node:
            continue
        fragment = "#/" + "/".join(x.replace("~", "~0").replace("/", "~1") for x in path) if path else "#"
        local = Draft202012Validator({"$ref": schema["$id"] + fragment}, registry=registry,
                                    format_checker=FormatChecker())
        for index, example in enumerate(node["examples"]):
            errors = list(local.iter_errors(example))
            require(not errors, f"Invalid {schema['$id']}{fragment} example {index}: {errors[:1]}", "schema_examples")
            example_count += 1
    require(example_count > 0, f"No examples for {schema['$id']}")
    # Root envelopes are closed contracts: adding a field and removing their
    # discriminator must fail. Required-field mutation tests run on each root
    # example, using the actual oneOf branch that validated it.
    for example in schema.get("examples", []):
        if not isinstance(example, dict):
            continue
        invalid = {**example, "unexpected_uncontracted_field": True}
        require(not validator.is_valid(invalid), f"Open root envelope in {schema['$id']}", "schema_negative")
        branch = schema
        while True:
            if "$ref" in branch:
                branch = pointer(schema, branch["$ref"])
            elif "oneOf" in branch:
                matches = [candidate for candidate in branch["oneOf"]
                           if validator.evolve(schema=candidate).is_valid(example)]
                require(len(matches) == 1, "Example must select exactly one variant")
                branch = matches[0]
            else:
                break
        for name in branch.get("required", []):
            invalid = copy.deepcopy(example)
            invalid.pop(name, None)
            require(not validator.is_valid(invalid), f"Missing required {name} accepted", "schema_negative")


def validate_control_negatives(control: dict, registry: Registry) -> None:
    validator = Draft202012Validator(control, registry=registry, format_checker=FormatChecker())
    examples = {x["record_type"]: x for x in control["examples"]}
    mutations = [
        ("stream", ("scope", "vehicle_classes"), ["caravan"]),
        ("stream", ("scope", "market_countries"), ["US"]),
        ("stream", ("scope", "vehicle_classes"), ["car", "car"]),
        ("stream", ("scope", "source_filters_fingerprint"), "a" * 16),
        ("source_registration", ("source_id",), "01992000-0000-4000-8000-000000000001"),
        ("source_registration", ("recorded_at",), "not-a-dateZ"),
        ("source_registration", ("recorded_at",), "2026-09-07T10:00:00+02:00"),
        ("surface", ("allowed_origins",), ["http://unsafe.example"]),
        ("surface", ("allowed_origins",), ["https://user:password@publisher.example"]),
        ("surface", ("credential_ref",), None),
        ("capability_manifest", ("result_cap",), None),
        ("capability_manifest", ("result_cap_status",), "verified_unbounded"),
        ("admission_decision", ("rights", "acquire_inventory"), False),
        ("admission_decision", ("status",), "revoked"),
        ("admission_decision", ("status",), "expired"),
        ("admission_decision", ("status",), "blocked"),
        ("discovery_candidate", ("decision", "status"), "duplicate_candidate"),
        ("partition_plan", ("reconciliation_boundary",), "partition_only"),
        ("partition_plan", ("partitions",), []),
        ("partition_plan", ("plan_sha256",), "1"),
        ("partition_plan", ("partitions", 0, "partition_key"), "not-a-digest"),
    ]
    for record_type, path, value in mutations:
        mutated = copy.deepcopy(examples[record_type])
        target = mutated
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
        require(not validator.is_valid(mutated), f"Invalid control mutation accepted: {record_type}.{path}", "schema_negative")


def validate_control_semantics(control: dict) -> None:
    records = {x["record_type"]: x for x in control["examples"]}
    require(len(records) == len(control["examples"]), "Synthetic control bundle silently overwrites duplicate record types")

    def coherent(bundle: dict) -> bool:
        source, surface, stream = (bundle[x] for x in ("source_registration", "surface", "stream"))
        admission, capability, plan = (bundle[x] for x in ("admission_decision", "capability_manifest", "partition_plan"))
        start, end, review = (datetime.fromisoformat(admission[x].replace("Z", "+00:00"))
                              for x in ("valid_from", "valid_until", "review_due_at"))
        cap_start, cap_end = (datetime.fromisoformat(capability[x].replace("Z", "+00:00"))
                              for x in ("capabilities_verified_at", "revalidate_at"))
        return all([
            start < end,
            start <= review <= end,
            cap_start < cap_end,
            source["source_id"] == surface["source_id"] == stream["source_id"],
            surface["surface_id"] == stream["surface_id"] == admission["surface_id"] == capability["surface_id"],
            surface["admission_id"] == stream["admission_id"] == admission["admission_id"],
            stream["capability_manifest_id"] == capability["capability_manifest_id"] == plan["capability_manifest_id"],
            plan["stream_id"] == stream["stream_id"],
            plan["scope_revision"] == stream["scope"]["scope_revision"],
            set(stream["scope"]["market_countries"]) <= set(admission["scope"]["market_countries"]),
            set(stream["scope"]["vehicle_classes"]) <= set(admission["scope"]["vehicle_classes"]),
            len(plan["partitions"]) <= plan["max_partitions"],
            len(plan["partitions"]) == len({p["partition_id"] for p in plan["partitions"]}),
            len(plan["partitions"]) == len({p["partition_key"] for p in plan["partitions"]}),
        ])

    require(coherent(records), "Incoherent control example bundle", "control_semantics")
    mutations = [
        ("admission_decision", ("valid_until",), "2026-09-06T00:00:00Z"),
        ("admission_decision", ("review_due_at",), "2027-01-01T00:00:00Z"),
        ("admission_decision", ("scope", "vehicle_classes"), ["car"]),
        ("stream", ("source_id",), "01992000-0000-7000-8000-999999999999"),
        ("partition_plan", ("scope_revision",), "2"),
        ("capability_manifest", ("revalidate_at",), "2026-09-07T10:00:00Z"),
        ("partition_plan", ("partitions",), records["partition_plan"]["partitions"] * 2),
    ]
    for kind, path, value in mutations:
        broken = copy.deepcopy(records)
        target = broken[kind]
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
        require(not coherent(broken), f"Cross-field mismatch accepted: {kind}/{path}", "control_semantics")

    def allows_acquire(bundle, now):
        admission, capability = bundle["admission_decision"], bundle["capability_manifest"]
        def instant(value):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        now_at = instant(now)
        return (coherent(bundle)
                and bundle["source_registration"]["lifecycle"] in {"onboarding", "monitored", "degraded"}
                and admission["status"] == "approved" and admission["rights"]["acquire_inventory"]
                and instant(admission["valid_from"]) <= now_at < min(instant(admission["valid_until"]), instant(admission["review_due_at"]))
                and instant(capability["capabilities_verified_at"]) <= now_at < instant(capability["revalidate_at"]))

    now = "2026-09-07T12:00:00Z"
    require(allows_acquire(records, now), "Synthetic admitted onboarding fixture cannot acquire", "control_semantics")
    for lifecycle in ["candidate", "classified", "assessed", "admitted", "blocked", "quarantined", "suspended", "retired", "rejected"]:
        denied = copy.deepcopy(records)
        denied["source_registration"]["lifecycle"] = lifecycle
        require(not allows_acquire(denied, now), f"Non-operational lifecycle allowed acquisition: {lifecycle}", "control_semantics")
    for status in ["blocked", "expired", "revoked"]:
        denied = copy.deepcopy(records)
        denied["admission_decision"]["status"] = status
        # Even a corrupt cached rights=true cannot bypass current admission state.
        require(not allows_acquire(denied, now), f"Admission state allowed acquisition: {status}", "control_semantics")
    for boundary in ["2026-09-06T00:00:00Z", "2026-09-08T10:00:00Z", "2026-09-30T00:00:00Z", "2026-10-07T00:00:00Z"]:
        require(not allows_acquire(records, boundary), f"Stale/future admission/capability allowed at {boundary}", "control_semantics")


def validate_domain_negatives(domain: dict, registry: Registry) -> None:
    validator = Draft202012Validator(domain, registry=registry, format_checker=FormatChecker())
    examples = domain["examples"]
    for variant in domain["oneOf"]:
        require(any(validator.evolve(schema=variant).is_valid(example) for example in examples),
                f"Domain variant has no executable example: {variant}", "schema_branch_coverage")
    classes = {x["vehicle_class"] for x in examples if x.get("record_type") == "vehicle_profile"}
    require(classes == {"car", "lcv", "motorcycle", "motorhome", "unknown"},
            f"Incomplete vehicle variant examples: {classes}", "schema_branch_coverage")
    presence = next(x for x in examples if x.get("observation_kind") == "listing_presence")
    attributes = next(x for x in examples if x.get("observation_kind") == "listing_attribute_snapshot")
    invalid = copy.deepcopy(attributes)
    invalid["capture_evidence"] = presence["capture_evidence"]
    require(not validator.is_valid(invalid), "304 representation accepted as fresh attribute snapshot", "schema_negative")
    invalid = copy.deepcopy(presence)
    invalid["listing_identity"]["incarnation"] = "0"
    require(not validator.is_valid(invalid), "Zero incarnation accepted", "schema_negative")
    for value in [1, -1, "-1", "01", "1e3", "1.0", str(2 ** 64)]:
        invalid = {**presence, "seq": value}
        require(not validator.is_valid(invalid), f"Invalid sequence accepted: {value!r}", "schema_negative")
    number_validator = validator.evolve(schema={"$ref": "#/$defs/UInt64String"})
    rng = random.Random(20260907)
    boundaries = [0, 1, 2 ** 53 + 1, 2 ** 64 - 1, 2 ** 64]
    boundaries += [2 ** 64 + delta for delta in range(-20, 21)]
    boundaries += [rng.randrange(0, 10 ** 21) for _ in range(250)]
    for value in boundaries:
        require(number_validator.is_valid(str(value)) == (0 <= value < 2 ** 64),
                f"UInt64 regex differs from integer oracle: {value}", "schema_boundary")
    for profile in [x for x in examples if x.get("record_type") == "vehicle_profile"]:
        invalid = copy.deepcopy(profile)
        invalid["extension"]["extension_type"] = "not_the_declared_class"
        require(not validator.is_valid(invalid), "Cross-class extension accepted", "schema_negative")
        if profile["vehicle_class"] == "unknown":
            invalid = {**profile, "serving_state": "eligible"}
            require(not validator.is_valid(invalid), "Unknown class allowed into serving", "schema_negative")

    # Presence state and evidence basis form a closed semantic product. A schema
    # that validates each enum independently would admit nonsensical pairs.
    presence_validator = validator.evolve(schema={"$ref": "#/$defs/PresenceSignal"})
    allowed_presence = {
        ("observed_present", "detail_fetch"),
        ("observed_present", "index_membership"),
        ("observed_present", "conditional_not_modified"),
        ("source_reports_withdrawn", "explicit_tombstone"),
        ("locator_not_found", "detail_fetch"),
    }
    for state, basis in itertools.product(
            ["observed_present", "source_reports_withdrawn", "locator_not_found"],
            ["detail_fetch", "index_membership", "explicit_tombstone", "conditional_not_modified"]):
        signal = {"state": state, "basis": basis}
        require(presence_validator.is_valid(signal) == ((state, basis) in allowed_presence),
                f"Presence state/basis matrix mismatch: {state}/{basis}", "schema_negative")

    # Transport branches are exclusive. Offline imports must carry an importer
    # and manifest, never invented HTTP response metadata.
    raw_examples = [x for x in examples if x.get("record_type") == "raw_capture"]
    http_raw = next(x for x in raw_examples if x["transport"] == "http")
    import_raw = next(x for x in raw_examples if x["transport"] == "file_import")
    for field in ["response_status", "request_fingerprint_sha256", "headers_sha256"]:
        invalid = copy.deepcopy(import_raw)
        invalid[field] = 200 if field == "response_status" else "a" * 64
        require(not validator.is_valid(invalid), f"File import accepted HTTP field: {field}", "schema_negative")
    for field in ["import_manifest_sha256", "importer_ref"]:
        invalid = copy.deepcopy(import_raw)
        invalid.pop(field)
        require(not validator.is_valid(invalid), f"File import accepted without {field}", "schema_negative")
        invalid = copy.deepcopy(http_raw)
        invalid[field] = "a" * 64 if field.endswith("sha256") else "importer:invalid-mixed-branch"
        require(not validator.is_valid(invalid), f"HTTP capture accepted import field: {field}", "schema_negative")
    for field in ["response_status", "request_fingerprint_sha256", "headers_sha256"]:
        invalid = copy.deepcopy(http_raw)
        invalid.pop(field)
        require(not validator.is_valid(invalid), f"HTTP capture accepted without {field}", "schema_negative")

    # A complete attribute snapshot cannot declare media covered while marking
    # the media manifest unavailable, nor publish a complete manifest outside
    # its declared section set.
    invalid = copy.deepcopy(attributes)
    invalid["media_manifest"] = {"coverage": "unavailable", "ordering_asserted": False, "items": []}
    require(not validator.is_valid(invalid), "Covered media accepted with unavailable manifest", "schema_negative")
    invalid = copy.deepcopy(attributes)
    invalid["snapshot_scope"]["covered_sections"].remove("media")
    require(not validator.is_valid(invalid), "Complete media manifest accepted outside declared scope", "schema_negative")

    # Every general semantic role fixes its subject kind. Price is a distinct
    # Offer-only assertion and Money cannot be smuggled through a general field.
    general = next(x for x in attributes["field_assertions"] if x["semantic_role"] == "vehicle_attribute")
    general_validator = validator.evolve(schema={"$ref": "#/$defs/GeneralFieldAssertion"})
    role_subjects = {
        "listing_attribute": "listing",
        "offer_non_price_attribute": "offer",
        "vehicle_attribute": "vehicle",
        "publisher_attribute": "publisher_account",
        "seller_attribute": "seller",
        "legal_entity_attribute": "legal_entity",
        "point_of_sale_attribute": "point_of_sale",
        "classification": "vehicle",
    }
    for role, entity_kind in role_subjects.items():
        candidate = copy.deepcopy(general)
        candidate["semantic_role"] = role
        candidate["subject"]["entity_kind"] = entity_kind
        require(general_validator.is_valid(candidate), f"General role rejected its subject: {role}/{entity_kind}",
                "schema_negative")
        candidate["subject"]["entity_kind"] = "listing" if entity_kind != "listing" else "vehicle"
        require(not general_validator.is_valid(candidate), f"General role accepted wrong subject: {role}",
                "schema_negative")
    invalid = copy.deepcopy(general)
    invalid["field_path"] = "/offer/price"
    require(not general_validator.is_valid(invalid), "General assertion accepted reserved price path", "schema_negative")
    price = next(x for x in attributes["field_assertions"] if x["semantic_role"] == "offer_price")
    invalid = copy.deepcopy(price)
    invalid["subject"]["entity_kind"] = "vehicle"
    require(not validator.is_valid({**attributes, "field_assertions": [invalid]}),
            "Offer price accepted non-Offer subject", "schema_negative")
    invalid = copy.deepcopy(general)
    invalid["value"] = copy.deepcopy(price["value"])
    require(not general_validator.is_valid(invalid), "General assertion accepted Money", "schema_negative")

    # Identity action shapes are not interchangeable: rejecting a candidate
    # match makes no membership change, while assign must carry an assignment.
    identity = next(x for x in examples if x.get("record_type") == "identity_resolution_decision")
    reject_match = copy.deepcopy(identity)
    reject_match["action"] = "reject_match"
    reject_match["input_vehicle_ids"] = copy.deepcopy(identity["output_vehicle_ids"])
    reject_match["output_vehicle_ids"] = []
    reject_match["membership_changes"] = []
    require(validator.is_valid(reject_match), "Reject-match without membership mutation was rejected", "schema_negative")
    invalid = copy.deepcopy(reject_match)
    invalid["membership_changes"] = copy.deepcopy(identity["membership_changes"])
    require(not validator.is_valid(invalid), "Reject-match accepted membership mutation", "schema_negative")
    invalid = copy.deepcopy(identity)
    invalid["membership_changes"][0]["operation"] = "unassign"
    invalid["membership_changes"][0]["from_vehicle_id"] = invalid["membership_changes"][0].pop("to_vehicle_id")
    require(not validator.is_valid(invalid), "Assign decision accepted unassign membership operation", "schema_negative")

    # Quantity is intentionally broad for raw assertions; served profile fields
    # select a field-specific unit contract. Exercise both sides of every one.
    assertion_id = "01992090-0000-7000-8000-000000000001"
    unit_contracts = [
        ("AttributedDistanceQuantity", ["km", "mi"], "kg"),
        ("AttributedPowerQuantity", ["kW"], "mi"),
        ("AttributedMassQuantity", ["kg"], "m3"),
        ("AttributedVolumeQuantity", ["m3"], "kg"),
        ("AttributedEngineDisplacementQuantity", ["cm3"], "km"),
    ]
    for definition, valid_units, invalid_unit in unit_contracts:
        unit_validator = validator.evolve(schema={"$ref": f"#/$defs/{definition}"})
        for unit in valid_units:
            value = {"value": {"value_type": "quantity", "amount": "1", "unit": unit},
                     "assertion_id": assertion_id}
            require(unit_validator.is_valid(value), f"{definition} rejected valid unit {unit}", "schema_negative")
        invalid = {"value": {"value_type": "quantity", "amount": "1", "unit": invalid_unit},
                   "assertion_id": assertion_id}
        require(not unit_validator.is_valid(invalid),
                f"{definition} accepted invalid unit {invalid_unit}", "schema_negative")


def validate_openapi(registry: Registry) -> dict:
    document = yaml.load((ROOT / "contracts/openapi.yaml").read_text(encoding="utf-8"), Loader=UniqueYamlLoader)
    require(str(document.get("openapi", "")).startswith("3.1."), "OpenAPI dialect must be pinned 3.1.x")
    require(bool(document.get("paths")), "Empty OpenAPI paths")
    operations = []
    for path, item in document["paths"].items():
        for method, operation in item.items():
            if method not in {"get", "post", "put", "patch", "delete", "head", "options", "trace"}:
                continue
            operations.append(operation["operationId"])
            require(bool(operation.get("responses")), f"Missing responses: {method} {path}", "api_operations")
            require(bool(operation.get("security", document.get("security"))), f"Unauthenticated operation: {method} {path}")
            parameters = item.get("parameters", []) + operation.get("parameters", [])
            resolved = [pointer(document, p["$ref"]) if "$ref" in p else p for p in parameters]
            for name in re.findall(r"\{([^}]+)\}", path):
                require(any(p.get("in") == "path" and p.get("name") == name and p.get("required") is True for p in resolved),
                        f"Missing required path parameter: {path}:{name}")
    require(len(operations) == len(set(operations)), "Duplicate operationId")
    for _, node in walk(document):
        if isinstance(node, dict) and "$ref" in node:
            reference = node["$ref"]
            if reference.startswith("#"):
                pointer(document, reference)
                CHECKS["api_refs"] += 1
            else:
                # External schema references must be local repository files.
                filename, _, fragment = reference.partition("#")
                require(not re.match(r"[a-z]+://", filename), f"Runtime remote reference: {reference}")
                target = (ROOT / "contracts" / filename).resolve()
                require(target.is_relative_to(ROOT) and target.is_file(), f"Invalid local reference {reference}")
                pointer(json.loads(target.read_text(encoding="utf-8")), "#" + fragment)
    uri = (ROOT / "contracts/openapi.yaml").as_uri()
    api_registry = registry.with_resource(uri, Resource(document, DRAFT202012))
    for path, node in walk(document):
        if not isinstance(node, dict):
            continue
        examples = []
        schema_path = None
        if len(path) >= 3 and path[:2] == ("components", "schemas") and len(path) == 3:
            Draft202012Validator.check_schema(node)
            schema_path = path
            examples.extend(node.get("examples", []))
            if "example" in node:
                examples.append(node["example"])
        elif "schema" in node and "content" in path:
            schema_path = path + ("schema",)
            if "example" in node:
                examples.append(node["example"])
            for example in node.get("examples", {}).values():
                if isinstance(example, dict) and "value" in example:
                    examples.append(example["value"])
        if schema_path:
            fragment = "#/" + "/".join(x.replace("~", "~0").replace("/", "~1") for x in schema_path)
            validator = Draft202012Validator({"$ref": uri + fragment}, registry=api_registry,
                                            format_checker=FormatChecker())
            for example in examples:
                errors = list(validator.iter_errors(example))
                require(not errors, f"OpenAPI example {fragment}: {errors[:1]}", "api_examples")
    require(CHECKS["api_examples"] > 0, "No API examples validated")
    integer_schema = Draft202012Validator({"$ref": uri + "#/components/schemas/UInt64String"},
                                         registry=api_registry, format_checker=FormatChecker())
    for number in [0, 1, 2 ** 53 + 1, 2 ** 64 - 1, 2 ** 64, 10 ** 20 - 1]:
        require(integer_schema.is_valid(str(number)) == (0 <= number < 2 ** 64),
                f"API UInt64 wire bound differs from domain: {number}", "api_negative")
    for invalid in [1, -1, "-1", "01", "1e3", "1.0"]:
        require(not integer_schema.is_valid(invalid), f"API accepts invalid uint64: {invalid!r}", "api_negative")
    base_manifest = copy.deepcopy(document["components"]["schemas"]["BulkManifest"]["examples"][0])
    for name in ["BulkManifest", "ListingBulkManifest"]:
        shape = document["components"]["schemas"][name]
        manifest = {key: value for key, value in base_manifest.items() if key in shape["properties"]}
        if name == "ListingBulkManifest":
            manifest.update(stream_inventory_id="018f47a2-7b5c-7f10-8a2b-1c3d4e5f6070",
                            stream_id="018f47a2-7b5c-7f10-8a2b-1c3d4e5f6071",
                            item_schema="#/components/schemas/ListingView")
        manifest_validator = Draft202012Validator({"$ref": uri + "#/components/schemas/" + name},
                                                 registry=api_registry, format_checker=FormatChecker())
        require(manifest_validator.is_valid(manifest), f"Nonempty bulk fixture invalid: {name}", "api_examples")
        require(manifest_validator.is_valid({**manifest, "total_items": "0", "parts": []}),
                f"Legitimate empty bulk cannot be represented: {name}", "api_examples")
        require(not manifest_validator.is_valid({**manifest, "total_items": "0"}),
                f"Zero total with nonempty parts accepted: {name}", "api_negative")
        require(not manifest_validator.is_valid({**manifest, "parts": []}),
                f"Nonzero total without parts accepted: {name}", "api_negative")
    try:
        from openapi_spec_validator import validate
    except ImportError as exc:
        raise RuntimeError("Install pinned tools/requirements-verify.txt in an isolated environment for full OpenAPI validation") from exc
    validate(document, base_uri=(ROOT / "contracts/openapi.yaml").as_uri())
    CHECKS["api_spec"] += 1
    return document


def presence_model(model: dict) -> dict:
    state = "observed_present"
    last_effective_at = 0
    last_positive_at = 0
    first_absence_at = None
    ledger = {}
    effects = 0
    for event in model["events"]:
        if event["scope"] != model["scope"]:
            continue
        if event["id"] in ledger and ledger[event["id"]] != event:
            raise ValueError("Conflicting payloads for one evidence ID")
        ledger[event["id"]] = event
    # Rebuild the bounded ledger in verified effective order. Production must
    # likewise compensate earlier projections when late evidence invalidates
    # an absence pair. 'effects' counts canonical state transitions, not writes.
    for event in sorted(ledger.values(), key=lambda e: (e["at"], e["id"])):
        at = event["at"]  # Verified evidence order in this finite model, NOT receipt time.
        if at < last_effective_at:
            continue
        previous = state
        if event["kind"] == "positive" or (event["kind"] == "census" and event.get("present")):
            state, first_absence_at = "observed_present", None
            last_positive_at = at
            last_effective_at = at
        elif event["kind"] == "withdraw":
            if event.get("verified"):
                state, first_absence_at = "withdrawn", None
                last_effective_at = at
        elif event["kind"] == "census":
            if not event["complete"] or not event["comparable"]:
                first_absence_at = None
            elif at > last_positive_at and state != "withdrawn":
                if state == "not_observed":
                    pass
                elif first_absence_at is None:
                    state, first_absence_at = "absence_suspected", at
                elif at - first_absence_at >= model["cadence"]:
                    state = "not_observed"
                last_effective_at = at
        if state != previous:
            effects += 1
    return {"state": state, "effects": effects}


def presence_permutations() -> None:
    def census(key, at, complete=True, scope="S1"):
        return {"kind": "census", "id": key, "at": at, "scope": scope,
                "complete": complete, "comparable": True, "present": False}

    schedules = [
        [census("C1", 10), census("C2", 20), census("C3", 30)],
        [census("C1", 10), census("C2", 20, False), census("C3", 30)],
        [census("C1", 10), {"kind": "positive", "id": "P1", "at": 15, "scope": "S1"}, census("C3", 30)],
        [census("C1", 10, scope="OTHER"), census("C2", 20, scope="OTHER")],
    ]
    for schedule in schedules:
        base = {"scope": "S1", "cadence": 10, "events": schedule}
        expected = presence_model(base)
        for ordering in itertools.permutations(schedule + schedule[:1]):
            require(presence_model({**base, "events": ordering}) == expected,
                    "Late/duplicate presence evidence failed to converge", "model_assertions")
    conflict = [census("C1", 10), census("C1", 20)]
    try:
        presence_model({"scope": "S1", "cadence": 10, "events": conflict})
    except ValueError:
        CHECKS["model_assertions"] += 1
    else:
        raise AssertionError("Conflicting evidence IDs silently deduplicated")


def snapshot_model() -> None:
    live = {"A": 1, "B": 2, "C": 3}
    cut, snapshot = 3, tuple(sorted(live.items()))
    first_page = snapshot[:2]
    live["D"] = 4
    live.pop("B")
    events = [(4, "upsert", "D", 4), (5, "remove", "B", None)]
    second_page = snapshot[2:]
    reconstructed = dict(first_page + second_page)
    require(list(reconstructed) == ["A", "B", "C"], "Snapshot changed during paging", "model_assertions")
    for seq, op, key, value in events:
        if seq > cut:
            if op == "upsert":
                reconstructed[key] = value
            else:
                reconstructed.pop(key, None)
    require(reconstructed == live, "Snapshot/event handshake gap", "model_assertions")
    # Across every event order, a consumer that buffers by sequence converges.
    for delivery in itertools.permutations(events + events):
        unique = {event[0]: event for event in delivery}
        result = dict(snapshot)
        for _, op, key, value in sorted(unique.values()):
            if op == "upsert":
                result[key] = value
            else:
                result.pop(key, None)
        require(result == live, "Duplicate/out-of-order delivery diverged", "model_assertions")


def temporal_model(model: dict) -> None:
    domain = read_json("contracts/domain.schema.json")
    validator = Draft202012Validator({"$ref": domain["$id"] + "#/$defs/TimeInterval"},
                                    registry=registry_for([domain]), format_checker=FormatChecker())
    for fixture in model["intervals"]:
        interval = fixture["interval"]
        require(validator.is_valid(interval), "Time fixture is not a domain TimeInterval", "model_assertions")
        start = datetime.fromisoformat(interval["valid_from"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(interval["valid_to"].replace("Z", "+00:00")) if "valid_to" in interval else None
        # recorded_at remains an independent, well-formed clock; it cannot repair
        # an inverted source interval. This is not an as-of reconstruction engine.
        datetime.fromisoformat(fixture["recorded_at"].replace("Z", "+00:00"))
        accepted = end is None or start < end
        require(accepted == fixture["expected"], "Invalid temporal interval acceptance", "model_assertions")


def filtered_projection_model() -> None:
    # A finite full-after-image log. This proves the reference transform, not
    # database isolation, production cursor security or transaction durability.
    events = [("A", {"class": "car", "price": 10000}),
              ("B", {"class": "motorcycle", "price": 9000}),
              ("A", {"class": "car", "price": 21000}),
              ("B", {"class": "car", "price": 19000}),
              ("C", {"class": "car", "price": 25000}),
              ("A", None), ("C", {"class": "car", "price": 18000})]
    states = [{}]
    for key, after in events:
        state = copy.deepcopy(states[-1])
        if after is None:
            state.pop(key, None)
        else:
            state[key] = after
        states.append(state)
    predicates = [lambda row: row["class"] == "car" and row["price"] <= 20000,
                  lambda row: row["class"] == "motorcycle", lambda row: True]
    for predicate in predicates:
        def filtered(state):
            return {key: value for key, value in state.items() if predicate(value)}
        for cut in range(len(states)):
            client = copy.deepcopy(filtered(states[cut]))
            scanned_through = cut
            empty_pages = 0
            for seq in range(cut + 1, len(states)):
                key, after = events[seq - 1]
                before = states[seq - 1].get(key)
                # Each scan is a page, possibly carrying no visible event.
                if after is not None and predicate(after):
                    client[key] = copy.deepcopy(after)
                    client[key] = copy.deepcopy(after)  # duplicate delivery
                elif before is not None and predicate(before):
                    client.pop(key, None)
                    client.pop(key, None)
                else:
                    empty_pages += 1
                scanned_through = seq
                require(client == filtered(states[seq]), "Filtered prefix diverged", "model_assertions")
            require(client == filtered(states[-1]), "Filtered snapshot/event gap", "model_assertions")
            require(scanned_through == len(events), "Empty page failed to advance cursor", "model_assertions")
            if cut == 0 and predicate is predicates[1]:
                require(empty_pages > 0, "Fixture failed to exercise invisible changes", "model_assertions")


def ownership_model() -> None:
    # Durable flags/atomic gate are axioms here; integration fault injection
    # must prove that the chosen datastore implementation fulfils those axioms.
    for order in itertools.permutations(["D1", "D2"]):
        generations = {"D1": {1: {"L1"}}, "D2": {1: set()}}
        owners = {1: {"L1": "D1"}, 2: {"L1": "D2"}}
        epoch = 1
        expected_receipts = {dealer: {"transfer_id": "T1", "from_epoch": 1, "to_epoch": 2,
                                     "generation_id": dealer + "-G2"} for dealer in generations}
        receipts = {}

        def commit_if_ready(expected_epoch=1):
            nonlocal epoch
            if (epoch == expected_epoch
                    and receipts == expected_receipts
                    and all(2 in versions for versions in generations.values())):
                epoch = 2
                return True
            return False

        def visible():
            return {dealer for dealer, versions in generations.items() if "L1" in versions[epoch]}

        require(not commit_if_ready(), "Unprepared transfer committed", "model_assertions")
        for dealer in order:
            require(epoch == 1, "Ownership changed before preparation", "model_assertions")
            generations[dealer][2] = set() if dealer == "D1" else {"L1"}
            receipts[dealer] = copy.deepcopy(expected_receipts[dealer])
            if dealer == order[0]:
                require(not commit_if_ready(), "Partly prepared transfer committed", "model_assertions")
            require(visible() == {"D1"}, "Prepared generation leaked before ownership gate", "model_assertions")
        # A restart discards process identity and reloads only the model's durable
        # generations/receipts. The real durability mechanism is not modelled.
        generations, receipts = copy.deepcopy(generations), copy.deepcopy(receipts)
        receipts["D2"]["transfer_id"] = "UNRELATED"
        require(not commit_if_ready(), "Receipt from another transfer admitted", "model_assertions")
        receipts["D2"] = copy.deepcopy(expected_receipts["D2"])
        receipts["D2"]["generation_id"] = "STALE-GENERATION"
        require(not commit_if_ready(), "Wrong generation receipt admitted", "model_assertions")
        receipts["D2"] = copy.deepcopy(expected_receipts["D2"])
        require(commit_if_ready(), "Fully prepared transfer cannot commit", "model_assertions")
        require(visible() == {"D2"}, "Ownership gate failed to move one current authority", "model_assertions")
        require(not commit_if_ready(expected_epoch=1), "Stale transfer CAS succeeded after commit", "model_assertions")

        def fenced_write(dealer, writer_epoch, members):
            if writer_epoch != epoch or any(owners[epoch].get(member) != dealer for member in members):
                return False
            generations[dealer][epoch] = set(members)
            return True

        require(not fenced_write("D1", 1, {"L1"}), "Old writer bypassed ownership fence", "model_assertions")
        require(not fenced_write("D1", 2, {"L1"}), "Current-epoch writer bypassed listing ownership", "model_assertions")
        require(fenced_write("D2", 2, {"L1"}), "Current owner cannot write its authorized member", "model_assertions")
        require(visible() == {"D2"}, "Rejected stale write changed ownership", "model_assertions")


def canonical_key_vector(model: dict) -> None:
    # This fixture is restricted to ASCII string keys/values, a subset where
    # Python's sorted compact JSON equals JCS. Do not use as a general JCS codec.
    data = model["input"]
    require(all(isinstance(k, str) and k.isascii() and isinstance(v, str) and v.isascii()
                for k, v in data.items()), "Run key fixture outside restricted canonical subset", "model_assertions")
    encoded = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    require(encoded == model["canonical_utf8"].encode("utf-8"), "Canonical run key bytes differ", "model_assertions")
    require(hashlib.sha256(encoded).hexdigest() == model["expected_sha256"], "Run key SHA-256 differs", "model_assertions")
    reversed_keys = dict(reversed(list(data.items())))
    require(json.dumps(reversed_keys, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") == encoded,
            "Property order changed canonical key", "model_assertions")
    changed = {**data, "window_end": "2026-09-09T00:00:00Z"}
    changed_bytes = json.dumps(changed, sort_keys=True, separators=(",", ":")).encode("utf-8")
    require(hashlib.sha256(changed_bytes).hexdigest() != model["expected_sha256"], "Changed run window retained key", "model_assertions")


def identity_model() -> None:
    observations = {"A": ("obs-a",), "B": ("obs-b",), "C": ("obs-c",)}
    active_edges = [("A", "B"), ("B", "C")]
    cannot = {frozenset({"A", "C"})}

    def clusters(edges, constraints):
        groups = [{key} for key in observations]
        for left, right in edges:
            a, b = next(x for x in groups if left in x), next(x for x in groups if right in x)
            merged = a | b
            if a is b or any(pair <= merged for pair in constraints):
                continue
            groups = [x for x in groups if x is not a and x is not b] + [merged]
        return {frozenset(x) for x in groups}

    require(clusters(active_edges, set()) == {frozenset({"A", "B", "C"})}, "Merge model failed", "model_assertions")
    require(clusters(active_edges[:1], cannot) == {frozenset({"A", "B"}), frozenset({"C"})}, "Split lost provenance", "model_assertions")
    for order in itertools.permutations(active_edges):
        require(all(not pair <= group for group in clusters(order, cannot) for pair in cannot),
                "Transitive merge violates cannot-link", "model_assertions")
    require(sum(len(x) for x in observations.values()) == 3, "Identity operation deleted evidence", "model_assertions")


def cursor_model() -> None:
    # Minimal signed-envelope threat model, not a production token implementation.
    fixture_key = b"synthetic-test-key-not-a-secret"
    context = {"tenant": "tenant-A", "scope": "dealer-1:car", "auth": "permission-v1", "snapshot": "snapshot-1"}
    claims = {**context, "expires": 900, "after": "item-B"}
    body = json.dumps(claims, sort_keys=True, separators=(",", ":")).encode()
    payload = base64.urlsafe_b64encode(body).decode()
    signature = hmac.new(fixture_key, payload.encode(), hashlib.sha256).hexdigest()
    token = payload + "." + signature

    def accepts(candidate: str, current: dict, now: int) -> bool:
        try:
            encoded, mac = candidate.split(".")
            expected = hmac.new(fixture_key, encoded.encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(mac, expected):
                return False
            decoded = json.loads(base64.urlsafe_b64decode(encoded))
            return decoded["expires"] > now and all(decoded[key] == value for key, value in current.items())
        except (ValueError, KeyError):
            return False

    require(accepts(token, context, 100), "Valid signed cursor rejected", "model_assertions")
    for key, bad in [("tenant", "tenant-B"), ("scope", "dealer-1:motorcycle"),
                     ("auth", "permission-v2"), ("snapshot", "snapshot-2")]:
        require(not accepts(token, {**context, key: bad}, 100), f"Cursor ignored {key}", "model_assertions")
    forged = base64.urlsafe_b64encode(json.dumps({**claims, "tenant": "tenant-B"}).encode()).decode() + "." + signature
    require(not accepts(forged, {**context, "tenant": "tenant-B"}, 100), "Modified claims accepted with original MAC", "model_assertions")
    require(not accepts(token, context, 900), "Cursor accepted at expiration boundary", "model_assertions")
    require(not accepts(token, context, 901), "Expired cursor accepted", "model_assertions")


def validate_capacity(model: dict) -> None:
    require(bool(model.get("contract_version")), "Capacity model lacks version")
    require(model.get("status") == "planning_assumptions_not_benchmarks", "Capacity cannot imply measured performance")
    require(model.get("pricing", {}).get("estimated_monthly_cost") is None, "Unpriced model must not invent a bill")
    common, equations = model["common_assumptions"], model["equations"]
    require(common["seconds_per_day"] == 86400, "Unexpected day conversion")
    require(common["recovery_clear_hours"] > 0, "Recovery deadline must be positive")

    def arithmetic(expression: str, values: dict) -> float:
        def evaluate(node):
            if isinstance(node, ast.Expression):
                return evaluate(node.body)
            if isinstance(node, ast.Name):
                return values[node.id]
            if isinstance(node, ast.Constant) and type(node.value) in (int, float):
                return node.value
            if isinstance(node, ast.BinOp):
                left, right = evaluate(node.left), evaluate(node.right)
                if isinstance(node.op, ast.Add):
                    return left + right
                if isinstance(node.op, ast.Sub):
                    return left - right
                if isinstance(node.op, ast.Mult):
                    return left * right
                if isinstance(node.op, ast.Div):
                    return left / right
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
                allowed = {"ceil": math.ceil, "max": max, "min": min}
                if node.func.id in allowed:
                    return allowed[node.func.id](*(evaluate(arg) for arg in node.args))
            raise ValueError(f"Forbidden expression in capacity contract: {expression}")
        return evaluate(ast.parse(expression, mode="eval"))

    profiles = model["active_profiles"] + model["historical_profiles"] + model["freshness_sensitivity_profiles"]
    require({p["active_listings"] for p in model["active_profiles"]} >= {10000000, 100000000, 1000000000},
            "Missing active scaling profiles")
    require(any(p["historical_observations"] >= 10000000000 for p in model["historical_profiles"]),
            "Missing ten-billion historical profile")
    for profile in profiles:
        values = {**common, **{k: v for k, v in profile.items() if type(v) in (int, float)}}
        pending = dict(equations)
        while pending:
            progress = False
            for name, equation in list(pending.items()):
                try:
                    value = arithmetic(equation, values)
                except KeyError:
                    continue
                require(math.isfinite(value) and value >= 0, f"Invalid capacity result: {profile['profile_id']}/{name}")
                values[name] = value
                del pending[name]
                progress = True
            if not progress:
                break
        for name, expected in profile["calculated_expected"].items():
            require(name in values, f"Missing/resolution-blocked equation: {profile['profile_id']}/{name}")
            require(math.isclose(values[name], expected, rel_tol=1e-8, abs_tol=1e-5),
                    f"Capacity arithmetic mismatch {profile['profile_id']}/{name}: {values[name]} != {expected}", "capacity_equations")
        if "active_listings" in profile:
            require(values["recovery_fetch_qps"] >= values["steady_fetch_qps"], "Recovery slower than arrivals")
            # Independent queue-conservation oracle, not the string equation.
            backlog = values["steady_fetch_qps"] * common["recovery_backlog_hours"] * 3600
            surplus = values["recovery_fetch_qps"] - values["steady_fetch_qps"]
            require(surplus > 0 and math.isclose(backlog / surplus, common["recovery_clear_hours"] * 3600),
                    "Recovery cannot drain backlog within its declared deadline", "capacity_equations")
    require(common["steady_state_headroom_factor"] > 1, "No normal headroom")
    # With capacity equal to arrivals, conservation says the backlog never drains.
    arrival_rate, equal_capacity, backlog = 100, 100, 360000
    clear_seconds = math.inf if equal_capacity <= arrival_rate else backlog / (equal_capacity - arrival_rate)
    require(math.isinf(clear_seconds), "Zero-headroom recovery must remain unbounded", "model_assertions")


def validate_catalog(catalog: dict, capacity: dict) -> None:
    cases = catalog["cases"]
    ids = [x["id"] for x in cases]
    require(len(ids) == len(set(ids)), "Duplicate acceptance case ID")
    docs = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "docs").rglob("*.md"))
    definitions_text = re.sub(r"^## \d+\. Trazabilidad[^\n]*\n.*?(?=^## |\Z)", "", docs, flags=re.M | re.S)
    invariants = set(re.findall(r"^(?:\|\s*|- \*\*)((?:FND|DOM|INV|OPS)-\d{3})(?:\s*\||\s+—)",
                               definitions_text, flags=re.M))
    references = set(re.findall(r"\b(?:FND|DOM|INV|OPS)-\d{3}\b", docs))
    require(references <= invariants, f"Referenced but undefined invariant: {references - invariants}")
    covered = set()
    for case in cases:
        for field in ["id", "title", "invariants", "given", "when", "then", "stage"]:
            require(bool(case.get(field)), f"Incomplete acceptance case {case.get('id')}: {field}")
        require(set(case["invariants"]) <= invariants, f"Unknown invariant in {case['id']}")
        require(case["stage"] == ("model" if "model" in case else "implementation"), f"Mislabelled execution stage: {case['id']}")
        require(re.fullmatch(r"[AM]\d{3}", case["id"]) is not None, f"Invalid acceptance ID: {case['id']}")
        covered.update(case["invariants"])
        if "model" not in case:
            CHECKS["implementation_obligations"] += 1
            continue
        model = case["model"]
        if model["type"] == "presence":
            require(json.loads(case["then"]) == model["expected"], f"Catalog/model oracles disagree: {case['id']}")
            require(presence_model(model) == model["expected"], f"Presence model failed: {case['id']}", "model_assertions")
        elif model["type"] == "snapshot":
            snapshot_model()
        elif model["type"] == "identity":
            identity_model()
        elif model["type"] == "cursor":
            cursor_model()
        elif model["type"] == "capacity":
            validate_capacity(capacity)
        elif model["type"] == "temporal":
            temporal_model(model)
        elif model["type"] == "filtered_projection":
            filtered_projection_model()
        elif model["type"] == "ownership":
            ownership_model()
        elif model["type"] == "canonical_key_vector":
            canonical_key_vector(model)
        else:
            raise AssertionError(f"Unknown model type: {model['type']}")
        CHECKS["model_scenarios"] += 1
    foundation = {f"FND-{i:03}" for i in range(1, 17)}
    require(foundation <= covered, f"Uncovered foundational invariants: {foundation - covered}")
    validate_invariant_traceability(set(ids), invariants)
    presence_permutations()


def validate_invariant_traceability(case_ids: set[str], invariants: set[str]) -> None:
    # Canonical doc tables are the single mapping, not a second generated report.
    # A link is an acceptance obligation; it does NOT mean product code passed.
    families = {"DOM": "01-domain-and-evidence.md", "INV": "02-inventory-and-api.md",
                "OPS": "03-runtime-and-operations.md"}

    def expand(value: str, pattern: str):
        result = set()
        for match in re.finditer(pattern, value):
            prefix, first, last = match.groups()
            low, high = int(first), int(last or first)
            require(low <= high, f"Descending traceability range: {match.group()}")
            result.update(f"{prefix}{i:03}" for i in range(low, high + 1))
        return result

    for family, name in families.items():
        text = (ROOT / "docs/architecture" / name).read_text(encoding="utf-8")
        section = re.search(r"^## \d+\. Trazabilidad[^\n]*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
        require(section is not None, f"Missing canonical traceability table for {family}")
        traced = set()
        for line in section.group(1).splitlines():
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if not line.startswith("|") or len(cells) < 2:
                continue
            rules = expand(cells[0], rf"({family}-)(\d{{3}})(?:\s*(?:\.\.|–|-)\s*(?:{family}-)?(\d{{3}}))?")
            if not rules:
                continue
            refs = expand(cells[1], r"([AM])(\d{3})(?:\s*(?:\.\.|–|-)\s*(?:[AM])?(\d{3}))?")
            require(rules <= invariants, f"Unknown rule in {family} traceability: {rules - invariants}")
            require(bool(refs) and refs <= case_ids, f"Missing/unknown acceptance IDs for {rules}: {refs - case_ids}")
            traced.update(rules)
        defined = {rule for rule in invariants if rule.startswith(family + "-")}
        require(defined <= traced, f"Untraced {family} invariants: {defined - traced}")
        CHECKS["invariants_traced"] += len(traced)


def validate_docs_and_canvas() -> None:
    files = [ROOT / "README.md", ROOT / "AGENTS.md", ROOT / "CLAUDE.md"] + list((ROOT / "docs").rglob("*.md"))
    for path in files:
        text = path.read_text(encoding="utf-8")
        require("\ufffd" not in text, f"Invalid encoding replacement in {path}")
        for target in re.findall(r"(?<!!)\[[^\]\n]*\]\(([^)\n]+)\)", text):
            target = target.strip("<>").split("#", 1)[0]
            if not target or re.match(r"(?:https?|mailto|obsidian):", target):
                continue
            # Historical evidence links may point read-only outside Cardeex;
            # they are provenance, not source files for the graph.
            if re.match(r"/?[A-Za-z]:", target):
                continue
            resolved = (path.parent / unquote(target)).resolve()
            require(resolved.is_relative_to(ROOT) and resolved.exists(), f"Broken local link in {path.name}: {target}", "doc_links")
        for link in re.findall(r"\[\[([^\]\n]+)\]\]", text):
            destination = link.split("|", 1)[0]
            filename, _, heading = destination.partition("#")
            target = (ROOT / filename).resolve() if filename else path
            if not target.suffix:
                target = target.with_suffix(".md")
            require(target.is_relative_to(ROOT) and target.is_file(), f"Broken/external vault link: {destination}", "doc_links")
            if heading and target.suffix == ".md":
                def slug(value):
                    return re.sub(r"\s+", "-", re.sub(r"[^\w\s-]", "", unquote(value).lower())).strip("-")
                headings = re.findall(r"^#{1,6}\s+(.+)$", target.read_text(encoding="utf-8"), flags=re.MULTILINE)
                require(slug(heading) in {slug(h) for h in headings}, f"Missing vault heading: {destination}", "doc_links")
    canvas = read_json("docs/knowledge/Cardeex-Foundation.canvas")
    nodes = {x["id"] for x in canvas["nodes"]}
    require(len(nodes) == len(canvas["nodes"]), "Duplicate canvas node ID")
    edge_ids = [x["id"] for x in canvas["edges"]]
    require(len(edge_ids) == len(set(edge_ids)), "Duplicate canvas edge ID")
    for edge in canvas["edges"]:
        require(edge["fromNode"] in nodes and edge["toNode"] in nodes, f"Dangling canvas edge {edge['id']}", "canvas")
    for node in canvas["nodes"]:
        if node["type"] == "file":
            target = (ROOT / node["file"]).resolve()
            require(target.is_relative_to(ROOT) and target.is_file(), f"External/missing canvas file: {node['file']}", "canvas")

    workflow = yaml.load((ROOT / ".github/workflows/verify-foundation.yml").read_text(encoding="utf-8"),
                         Loader=UniqueYamlLoader)
    require(workflow["permissions"] == {"contents": "read"}, "Verification CI has expanded permissions", "ci_contract")
    require(set(workflow["on"]) == {"push", "pull_request", "workflow_dispatch"}, "Unexpected CI trigger", "ci_contract")
    job = workflow["jobs"]["specification"]
    require(job["timeout-minutes"] <= 10, "Unbounded verification CI", "ci_contract")
    for step in job["steps"]:
        if "uses" in step:
            require(re.fullmatch(r"actions/(?:checkout|setup-python)@[a-f0-9]{40}", step["uses"]) is not None,
                    "Unpinned or unexpected verification action", "ci_contract")
    require(any(step.get("run") == "python tools/verify_foundation.py" for step in job["steps"]),
            "CI does not run the canonical verifier", "ci_contract")


def main() -> None:
    def deny_network(event: str, args: tuple) -> None:
        if event in {"socket.connect", "socket.connect_ex", "socket.getaddrinfo"}:
            raise RuntimeError("Specification verifier is offline; remote reference resolution is forbidden")

    sys.addaudithook(deny_network)
    require({"date-time", "uri", "uuid"} <= set(FormatChecker().checkers),
            "Missing format validators: install jsonschema[format] from tools/requirements-verify.txt")
    control, domain = read_json("contracts/control.schema.json"), read_json("contracts/domain.schema.json")
    registry = registry_for([control, domain])
    validate_examples(control, registry)
    validate_examples(domain, registry)
    validate_control_negatives(control, registry)
    validate_control_semantics(control)
    validate_domain_negatives(domain, registry)
    validate_openapi(registry)
    validate_catalog(read_json("contracts/acceptance-cases.json"), read_json("contracts/capacity-model.json"))
    validate_docs_and_canvas()
    print("PASS: Cardeex design specification checks")
    for key, value in sorted(CHECKS.items()):
        print(f"  {key}: {value}")
    print("Scope: schemas, API description, arithmetic and finite design models. No production or scale certification.")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"FAIL: {type(error).__name__}: {error}", file=sys.stderr)
        sys.exit(1)
