"""Small value objects for discovery claims, never canonical dealer identities."""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import re
import secrets
import time
from uuid import UUID


CLASSES = ("car", "lcv", "motorcycle", "motorhome")
KINDS = ("source", "publisher_account", "professional_seller", "point_of_sale", "unknown")
SOURCE_TYPES = ("marketplace", "dealer_owned", "manufacturer_inventory", "auction",
                "classifieds", "salvage_complete_vehicles", "authorized_aggregator", "unknown")
DECISIONS = ("unreviewed", "accepted", "duplicate_candidate", "rejected", "needs_evidence")
CONTRACT_COUNTRIES = ("ES", "FR", "DE", "NL", "BE", "CH")


class Conflict(ValueError):
    """A stale operation, policy restriction or incompatible contract blocked a write."""


def new_id() -> str:
    """RFC 9562 UUIDv7, opaque with 74 random bits; no entity data encoded."""
    millis = time.time_ns() // 1_000_000
    value = (millis << 80) | (7 << 76) | (secrets.randbits(12) << 64)
    return str(UUID(int=value | (2 << 62) | secrets.randbits(62)))


def timestamp(value: str | None = None) -> str:
    parsed = datetime.now(timezone.utc) if value is None else datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must have timezone")
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(".000000", "").replace("+00:00", "Z")


def instant(value: str) -> datetime:
    return datetime.fromisoformat(timestamp(value).replace("Z", "+00:00"))


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def locator(value: str) -> str:
    """Conservative observation normalization. Never changes http to https or drops filters."""
    from .transport import normalize_url
    return normalize_url(value)


@dataclass(frozen=True, slots=True)
class Observation:
    locator: str
    kind: str
    method: str
    method_version: str
    origin_locator: str
    evidence_group: str
    observed_at: str
    expires_at: str
    policy_ref: str
    countries: tuple[str, ...] = ()
    classes: tuple[str, ...] = ()
    source_type: str = "unknown"
    locality_code: str = ""
    locality_country: str = ""
    signals: dict = field(default_factory=dict)
    body_sha256: str | None = None

    def __post_init__(self):
        object.__setattr__(self, "locator", locator(self.locator))
        object.__setattr__(self, "origin_locator", locator(self.origin_locator))
        for name in ("observed_at", "expires_at"):
            object.__setattr__(self, name, timestamp(getattr(self, name)))
        if instant(self.expires_at) <= instant(self.observed_at):
            raise ValueError("retention expiry must follow observation")
        if self.kind not in KINDS or self.source_type not in SOURCE_TYPES:
            raise ValueError("unknown kind or source type")
        if not isinstance(self.countries, (tuple, list)) or any(not isinstance(c, str) or not re.fullmatch(r"[A-Z]{2}", c) for c in self.countries):
            raise ValueError("country claims must use two uppercase letters")
        if not isinstance(self.classes, (tuple, list)) or any(c not in CLASSES for c in self.classes):
            raise ValueError("vehicle class outside active scope")
        object.__setattr__(self, "countries", tuple(sorted(set(self.countries))))
        object.__setattr__(self, "classes", tuple(sorted(set(self.classes))))
        for name, limit in (("method", 256), ("method_version", 64), ("evidence_group", 256), ("policy_ref", 256)):
            val = getattr(self, name)
            if not isinstance(val, str) or not val.strip() or len(val) > limit:
                raise ValueError(f"invalid {name}")
        if not isinstance(self.locality_code, str) or len(self.locality_code) > 128:
            raise ValueError("invalid locality code")
        if not isinstance(self.locality_country, str) or (self.locality_country and not re.fullmatch(r"[A-Z]{2}", self.locality_country)):
            raise ValueError("locality country must be explicit ISO-shaped claim or unknown")
        if not isinstance(self.signals, dict) or len(canonical_json(self.signals)) > 16384:
            raise ValueError("signals must be a bounded JSON object")
        if self.body_sha256 is not None and not re.fullmatch(r"[a-f0-9]{64}", self.body_sha256):
            raise ValueError("invalid artifact checksum")

    def as_dict(self) -> dict:
        return asdict(self)

    @property
    def candidate_key(self) -> str:
        return digest([self.locator, self.kind])

    @property
    def evidence_key(self) -> str:
        identity = self.as_dict()
        identity.pop("expires_at")
        identity.pop("policy_ref")
        return digest(identity)
