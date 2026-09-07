"""Bounded, offline technology observations from the already retained response.

Authored for Cardeex; sources consulted 2026-09-07 are recorded in SOURCE_REFS.
Rules recognize observed generator declarations, asset paths and embedded host
relationships. They are hypotheses, not a vendor fingerprint certification,
inventory recipe, access permission, entity match or business-activity verdict.
Paths are configurable and declarations can be forged or stale. No endpoint is
constructed or requested, no script is evaluated and no source is fetched here.

DealerWebs (dealerwebs.com) is distinct from Dealerweb (dealerweb.org); the latter
has no implemented signature. A documented provider host inside an actual
iframe/script is a tentative relationship, not proof that a named widget exists.
The synthetic test URLs are inputs, never a catalogue of working endpoints.
"""

from html.parser import HTMLParser
import re
from urllib.parse import unquote, urlsplit

from .transport import normalize_url


VERSION = "discovery-technology/1"
MAX_DOCUMENT_BYTES = 2 * 1024 * 1024
MAX_NODES = 10000
MAX_DEPTH = 128
MAX_VALUE = 192
MAX_DETECTIONS = 10
MAX_SURFACES = 8
MAX_EVIDENCE = 2
MAX_REFERENCES = 256

SOURCE_REFS = {
    "wordpress_generator": "https://developer.wordpress.org/reference/functions/get_the_generator/",
    "wordpress_assets": "https://developer.wordpress.org/plugins/plugin-basics/determining-plugin-and-content-directories/",
    "drupal_generator": "https://api.drupal.org/api/drupal/core!lib!Drupal!Core!EventSubscriber!ResponseGeneratorSubscriber.php/class/ResponseGeneratorSubscriber/11.x",
    "joomla_generator": "https://github.com/joomla/joomla-cms/blob/5.4-dev/libraries/src/Document/Document.php",
    "next_assets": "https://nextjs.org/docs/pages/api-reference/config/next-config-js/assetPrefix",
    "next_header": "https://nextjs.org/docs/app/api-reference/config/next-config-js/poweredByHeader",
    "nuxt_assets": "https://nuxt.com/docs/3.x/api/nuxt-config",
    "nuxt_rendering": "https://nuxt.com/docs/4.x/guide/concepts/rendering",
    "autoscout_provider": "https://www.autoscout24.de/haendlerportal/haendlerhomepage/",
    "dealerwebs_provider": "https://www.dealerwebs.com/",
    "html_parsing": "https://html.spec.whatwg.org/multipage/parsing.html",
}

_GENERATORS = ((r"^wordpress(?:\s|$)", "WordPress", "wordpress_generator"),
               (r"^drupal(?:\s|$)", "Drupal", "drupal_generator"),
               (r"^joomla(?:!|\s|$)", "Joomla", "joomla_generator"))
_INVENTORY = re.compile(r"(?:^|[/\W_])(stock|inventory|inventario|vehiculos|véhicules|fahrzeuge|fahrzeugbestand|gebrauchtwagen|occasions|aanbod|voertuigen|used-cars|used-vans)(?:$|[/\W_])", re.I)
_DETAIL = re.compile(r"/(?:car|vehicle|listing|detail|anuncio|annonce|voiture|motorrad)/[^/?]+", re.I)
_PARTS = re.compile(r"(?:^|[/\W_])(parts|pieces|pièces|recambios|repuestos|ersatzteile|onderdelen)(?:$|[/\W_])", re.I)
_ASSET_SUFFIXES = (".js", ".css", ".png", ".jpg", ".jpeg", ".webp", ".svg", ".pdf", ".zip", ".woff", ".woff2")
_VOID = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"})
_INERT = frozenset({"script", "style", "template", "textarea", "pre", "code", "noscript", "title", "iframe",
                    "xmp", "noembed", "noframes", "plaintext"})


def _result():
    return {"version": VERSION, "status": "unknown", "detections": [], "inventory_surfaces": [],
            "dynamic_rendering_required": "unknown", "conflicts": [],
            "limitations": ["technology_does_not_certify_inventory_or_entity", "signatures_are_observed_unverified_hints",
                            "no_network_or_javascript_execution", "signature_catalogue_is_not_exhaustive"]}


def _limit(result, reason):
    if reason not in result["limitations"]:
        result["limitations"].append(reason)


class _NodeLimit(ValueError):
    pass


class _TechnologyHTML(HTMLParser):
    def __init__(self, result, origin):
        super().__init__(convert_charrefs=True)
        self.result, self.origin = result, origin
        self.nodes = 0
        self.counts = {}
        self.inert = []
        self.visible_characters = 0
        self.mount = False
        self.executable_script = False
        self.access_hint = False
        self.visible_media = False
        self.base = origin
        self.base_seen = False
        self.references = []
        self.plaintext = False

    def count_event(self):
        # End tags, data and ignored markup consume the same finite work budget.
        self.nodes += 1
        if self.nodes > MAX_NODES:
            raise _NodeLimit("document_node_limit")

    def push_inert(self, tag):
        if len(self.inert) >= MAX_DEPTH:
            raise _NodeLimit("document_depth_limit")
        self.inert.append(tag)

    def detect(self, category, name, selector, rule, value, source, confidence="tentative"):
        evidence = {"selector": selector, "rule": rule, "value": value}
        existing = next((d for d in self.result["detections"] if (d["category"], d["name"]) == (category, name)), None)
        if existing is None:
            if len(self.result["detections"]) >= MAX_DETECTIONS:
                _limit(self.result, "signal_limit")
                return
            existing = {"category": category, "name": name, "evidence": [], "confidence": confidence, "source_refs": []}
            self.result["detections"].append(existing)
        if evidence not in existing["evidence"]:
            if len(existing["evidence"]) >= MAX_EVIDENCE:
                _limit(self.result, "signal_limit")
            else:
                existing["evidence"].append(evidence)
        if source not in existing["source_refs"]:
            existing["source_refs"].append(source)
        if confidence == "strong_signal":
            existing["confidence"] = confidence

    def generator(self, value, selector):
        if not isinstance(value, str) or len(value) > MAX_VALUE:
            if value:
                _limit(self.result, "signal_string_limit")
            return
        for pattern, name, source in _GENERATORS:
            if re.match(pattern, value.strip(), re.I):
                # Retain the recognized token, not arbitrary header/meta content.
                self.detect("cms", name, selector, "generator_declaration", name, source, "strong_signal")

    def safe_url(self, value):
        if self.base is None and not re.match(r"^https?://", value, re.I):
            _limit(self.result, "unresolved_document_base")
            return None
        try:
            value = normalize_url(value, self.base or self.origin)
        except (ValueError, TypeError):
            _limit(self.result, "unsafe_or_secret_locator_omitted")
            return None
        if len(value) > MAX_VALUE:
            _limit(self.result, "signal_string_limit")
            return None
        return value

    def asset(self, url, selector):
        path = unquote(urlsplit(url).path)
        if re.search(r"/(?:wp-content|wp-includes)/", path, re.I):
            self.detect("cms", "WordPress", selector, "wordpress_asset_path", url, "wordpress_assets")
        plugin = re.search(r"/wp-content/plugins/([a-z0-9][a-z0-9_-]{0,47})/", path, re.I)
        if plugin:
            self.detect("plugin", "wordpress-plugin:" + plugin[1].lower(), selector,
                        "observed_plugin_directory_not_certified_product", url, "wordpress_assets")
        if "/_next/static/" in path:
            self.detect("framework", "Next.js", selector, "next_asset_path", url, "next_assets")
        if "/_nuxt/" in path:
            self.detect("framework", "Nuxt", selector, "nuxt_asset_path", url, "nuxt_assets")

    def embedded_provider(self, url, selector):
        host = urlsplit(url).hostname
        # Exact host boundary; neither narrative links nor attacker suffixes match.
        for domain, name, source in (("autoscout24.de", "AutoScout24", "autoscout_provider"),
                                      ("autoscout24.ch", "AutoScout24", "autoscout_provider"),
                                      ("dealerwebs.com", "DealerWebs", "dealerwebs_provider")):
            if host == domain or host.endswith("." + domain):
                self.detect("inventory_provider", name, selector, "observed_embedded_provider_host", url, source)
                return name
        return None

    def surface(self, url, selector, rule):
        if any(item["locator"] == url for item in self.result["inventory_surfaces"]):
            return
        if len(self.result["inventory_surfaces"]) >= MAX_SURFACES:
            _limit(self.result, "signal_limit")
            return
        self.result["inventory_surfaces"].append({"locator": url, "selector": selector, "rule": rule,
            "confidence": "tentative", "requires_access_review": True, "inventory_certified": False})

    def handle_starttag(self, tag, attributes):
        self.count_event()
        if self.inert:
            if tag not in _VOID:
                self.push_inert(tag)
            return
        self.counts[tag] = self.counts.get(tag, 0) + 1
        selector = f"{tag}[{self.counts[tag]}]"
        names = [name for name, _ in attributes]
        duplicate = len(names) != len(set(names))
        if duplicate:
            _limit(self.result, "ambiguous_duplicate_attributes")
            attrs = {}
        elif len(attributes) > 64:
            _limit(self.result, "attribute_limit")
            attrs = {}
        else:
            attrs = dict(attributes)
        if tag in _INERT:
            self.push_inert(tag)
        if tag == "plaintext":
            self.plaintext = True
        if tag == "base" and "href" in attrs and not self.base_seen:
            self.base_seen = True
            try:
                self.base = normalize_url(attrs["href"], self.origin)
            except (ValueError, TypeError):
                self.base = None
                _limit(self.result, "unresolved_document_base")
        if tag == "meta" and (attrs.get("name") or "").lower() == "generator":
            self.generator(attrs.get("content"), selector + "@content")
        if tag == "input" and (attrs.get("type") or "").lower() == "password":
            self.access_hint = True
        if tag in ("div", "main") and attrs.get("id") in ("__next", "__nuxt"):
            self.mount = True
        if tag in ("img", "video", "audio", "canvas", "svg", "input"):
            self.visible_media = True
        script_type = (attrs.get("type") or "").lower()
        active_script = tag == "script" and script_type in ("", "module", "text/javascript", "application/javascript")
        if active_script and not duplicate:
            self.executable_script = True
        if tag == "script" and not active_script:
            return
        key = "href" if tag in ("a", "link") else "src" if tag in ("script", "iframe") else None
        if key is None or not attrs.get(key):
            return
        if len(self.references) >= MAX_REFERENCES:
            _limit(self.result, "signal_limit")
            return
        if len(attrs[key]) > MAX_VALUE:
            _limit(self.result, "signal_string_limit")
            return
        title = attrs.get("title") or ""
        if len(title) > MAX_VALUE:
            title = ""
            _limit(self.result, "signal_string_limit")
        rel = (attrs.get("rel") or "")[:MAX_VALUE]
        self.references.append((tag, attrs[key], selector + "@" + key, title, rel))

    def inspect_reference(self, tag, raw_url, selector, title, rel):
        # Resolve after parsing: the first active HTML base applies to every URL,
        # including references encountered before that base in malformed markup.
        url = self.safe_url(raw_url)
        if url is None:
            return
        if tag == "script" or (tag == "link" and set(rel.lower().split()) & {"stylesheet", "preload", "modulepreload"}):
            self.asset(url, selector)
        provider = self.embedded_provider(url, selector) if tag in ("iframe", "script") else None
        path = unquote(urlsplit(url).path)
        if tag in ("a", "iframe") and not path.lower().endswith(_ASSET_SUFFIXES) and not _DETAIL.search(path) and not _PARTS.search(path):
            if _INVENTORY.search(path + " " + title):
                self.surface(url, selector, "embedded_inventory_path_or_title" if tag == "iframe" else "observed_inventory_path")
            elif provider and tag == "iframe":
                self.surface(url, selector, "provider_embed_content_unverified")

    def handle_startendtag(self, tag, attrs):
        # HTML ignores a trailing slash on non-void start tags (WHATWG parsing).
        # In particular, <template/> and <script/> do not reactivate later text.
        # Foreign/XML edge cases remain conservatively inert, never executable.
        self.handle_starttag(tag, attrs)
        if tag in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        self.count_event()
        # PLAINTEXT has no end-tag transition; apparent closing tags are text.
        if self.plaintext:
            return
        if tag in self.inert:
            index = len(self.inert) - 1 - self.inert[::-1].index(tag)
            del self.inert[index:]

    def handle_data(self, data):
        self.count_event()
        if self.inert:
            return
        self.visible_characters += len(data.strip())
        lower = data.lower()
        if any(value in lower for value in ("access denied", "service unavailable", "login required", "verify you are human",
                                             "permanently closed", "cerrado permanentemente", "accès refusé", "zugriff verweigert")):
            self.access_hint = True

    def handle_comment(self, data):
        self.count_event()

    def handle_decl(self, decl):
        self.count_event()

    def handle_pi(self, data):
        self.count_event()

    def unknown_decl(self, data):
        self.count_event()

    def finish(self):
        self.close()
        for reference in self.references:
            self.inspect_reference(*reference)

    def finalize_claims(self, *, complete):
        # This bounded step runs even when tokenization stopped early. Conflicting
        # declarations cannot become strong claims simply because a limit fired.
        for category in ("cms", "framework", "inventory_provider"):
            items = [item for item in self.result["detections"] if item["category"] == category]
            if len(items) > 1:
                self.result["conflicts"].append({"category": category, "names": sorted(item["name"] for item in items),
                                                 "reason": "multiple_signatures_may_be_mixed_embedded_or_stale"})
                for item in items:
                    item["confidence"] = "tentative"
        if self.access_hint:
            _limit(self.result, "access_or_error_content_activity_unknown")
        elif (complete and self.mount and self.executable_script and not self.visible_media and self.visible_characters == 0
              and any(item["category"] == "framework" for item in self.result["detections"])):
            self.result["dynamic_rendering_required"] = True
            _limit(self.result, "dynamic_shell_unrendered")
        if not self.result["detections"]:
            _limit(self.result, "technology_unknown")
        if "signal_limit" in self.result["limitations"] or "signal_string_limit" in self.result["limitations"]:
            self.result["status"] = "partial"


def classify_technology(body: bytes, content_type: str, origin: str, headers: dict | None = None) -> dict:
    """Return bounded, serializable hypotheses; unsupported input returns unknown.

Evidence selectors refer to actual HTML elements or allowlisted response headers.
Confidence is qualitative, uncalibrated and never an identity/access decision.
``dynamic_rendering_required=True`` records an observed empty script shell only;
``unknown`` covers all other cases, including server-rendered framework pages.
Malformed/unsupported input never aborts a caller's independent generic parser.
"""
    result = _result()
    if not isinstance(body, bytes):
        _limit(result, "empty_or_invalid_document")
        return result
    if len(body) > MAX_DOCUMENT_BYTES:
        _limit(result, "document_byte_limit")
        return result
    if not body.strip():
        _limit(result, "empty_or_invalid_document")
        return result
    if not isinstance(content_type, str) or len(content_type) > 256 or content_type.split(";", 1)[0].strip().lower() not in ("text/html", "application/xhtml+xml"):
        _limit(result, "unsupported_document_type")
        return result
    try:
        origin = normalize_url(origin)
    except (ValueError, TypeError):
        _limit(result, "invalid_origin")
        return result
    charset = re.search(r"charset\s*=\s*[\"']?([\w-]+)", content_type, re.I)
    encoding = charset[1].lower() if charset else "utf-8-sig"
    if encoding not in ("utf-8", "utf8", "utf-8-sig", "iso-8859-1", "latin-1", "windows-1252", "cp1252"):
        _limit(result, "unsupported_encoding")
        return result
    try:
        document = body.decode(encoding)
    except UnicodeError:
        _limit(result, "invalid_encoding")
        return result
    if "\x00" in document:
        _limit(result, "binary_document")
        return result
    parser = _TechnologyHTML(result, origin)
    if headers is not None:
        if not isinstance(headers, dict) or len(headers) > 64:
            _limit(result, "header_limit_or_invalid_headers")
        else:
            for key, value in headers.items():
                if not isinstance(key, str):
                    continue
                if key.lower() == "x-generator":
                    parser.generator(value, "response-header:x-generator")
                elif key.lower() == "x-powered-by" and isinstance(value, str) and value.strip().lower() == "next.js":
                    parser.detect("framework", "Next.js", "response-header:x-powered-by", "powered_by_declaration",
                                  "Next.js", "next_header", "strong_signal")
    result["status"] = "analyzed"
    complete = False
    try:
        parser.feed(document)
        parser.finish()
        complete = True
    except _NodeLimit as exc:
        result["status"] = "partial"
        _limit(result, str(exc))
    except (ValueError, RecursionError, AssertionError):
        # HTMLParser raises AssertionError for malformed marked declarations;
        # never surface its raw source text or abort the independent caller.
        result["status"] = "partial"
        _limit(result, "invalid_or_ambiguous_html")
    parser.finalize_claims(complete=complete)
    return result
