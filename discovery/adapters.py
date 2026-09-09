"""Pure, bounded discovery extractors; input is retained bytes, never a live URL.

Outputs are claims about locators, not resolved entity identities, verified
activity, inventory records or coverage. Detail links are counted as evidence
for the containing surface; their vehicle records and photographs are not read.
"""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from urllib.parse import unquote, urlsplit

from .transport import normalize_url

PARSER_VERSION = "discovery-adapters/2"
MAX_DOCUMENT_BYTES = 2 * 1024 * 1024
MAX_NODES = 10000
MAX_CANDIDATES = 5000
MAX_DEPTH = 32
MAX_LABEL_LENGTH = 4096
_KINDS = {"source", "professional_seller", "point_of_sale", "publisher_account", "unknown"}
_INVENTORY = re.compile(r"(?:^|[/\W_])(stock|inventory|inventario|vehiculos|véhicules|fahrzeuge|gebrauchtwagen|occasion|occasions|aanbod|voertuigen|gebrauchte|used-cars|used-vans)(?:$|[/\W_])", re.I)
_LISTING = re.compile(r"/(?:vehicle|vehicles|vehicule|voiture|car|cars|auto|motorrad|motorcycle|listing|annonce|anuncio|detail)/(?:[^/?]+)", re.I)
_PARTS = re.compile(r"(?:^|[/\W_])(parts|pieces|pièces|recambios|repuestos|ersatzteile|onderdelen)(?:$|[/\W_])", re.I)
_MEDIA_SUFFIXES = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".svg", ".ico", ".bmp", ".tif", ".tiff", ".mp4", ".webm", ".mov", ".mp3", ".woff", ".woff2", ".css", ".js")


def discovery_exclusion(locator: str) -> str | None:
    """One boundary for HTML, structured records, sitemaps and queued work.

    Recognised details remain hints. Unrecognised routes are not certified as
    inventory-free; a source-specific reviewed channel may further narrow them.
    """
    parts = urlsplit(locator)
    paths = [unquote(parts.path), unquote(parts.fragment.split("?", 1)[0])]
    if any(path.lower().endswith(_MEDIA_SUFFIXES) for path in paths):
        return "media_or_asset"
    if any(_PARTS.search(path) for path in paths):
        return "parts_surface"
    if any(_LISTING.search(path) for path in paths):
        return "inventory_detail"
    return None


class ParserError(ValueError):
    """Document cannot support a successful discovery extraction."""


class ParserLimitError(ParserError):
    """Extraction exceeded a bound; partial results MUST NOT be treated as complete."""


class _Collector:
    def __init__(self,base):
        self.base=normalize_url(base)
        self.rows={}
        self.nodes=0

    def count(self):
        self.nodes+=1
        if self.nodes>MAX_NODES:
            raise ParserLimitError("Document node budget exceeded")

    def add(self,locator,kind="unknown",relation="outbound_link",label="",signals=None,selector="",method="observed_link"):
        if not isinstance(locator,str) or not locator.strip():
            return
        try:
            locator=normalize_url(locator,self.base)
        except ValueError:
            return
        exclusion = discovery_exclusion(locator)
        if exclusion == "media_or_asset":
            return
        signals = dict(signals or {})
        if exclusion == "parts_surface":
            signals.update(link_context="parts", exclusion_reason=exclusion, discovery_fetch_allowed=False)
        if exclusion == "inventory_detail" and signals.get("link_context") != "parts":
            signals.update(detail_link_hints=[locator] if len(locator) <= 1024 else [],
                           detail_links_encountered=1, detail_hints_truncated=len(locator) > 1024,
                           exclusion_reason=exclusion,
                           discovery_fetch_allowed=False, claim_status="unverified")
            locator, kind, relation = self.base, "source", "inventory_surface"
        if not isinstance(label,str):
            label=""
        label=" ".join(label.split())
        if len(label)>MAX_LABEL_LENGTH:
            raise ParserLimitError("Candidate label exceeded parsing budget")
        evidence=dict(signals or {})
        evidence.update(evidence_selector=selector,assertion_method=method,parser_version=PARSER_VERSION)
        row=dict(locator=locator,kind=kind,relation=relation,label=label,signals=evidence)
        key=(locator,kind,relation)
        if key not in self.rows:
            self.rows[key]=row
            if len(self.rows)>MAX_CANDIDATES:
                raise ParserLimitError("Candidate budget exceeded")
        else:
            existing=self.rows[key]
            # Duplicate anchors retain the first selector; claim corroboration is
            # represented separately by the registry's source observation.
            if not existing["label"] and label:
                existing["label"]=label
            if "detail_link_hints" in evidence:
                hints = existing["signals"].setdefault("detail_link_hints", [])
                existing["signals"]["detail_links_encountered"] = existing["signals"].get("detail_links_encountered", 0) + 1
                for hint in evidence["detail_link_hints"]:
                    if hint not in hints:
                        if len(hints) < 8 and sum(map(len, hints)) + len(hint) <= 4096:
                            hints.append(hint)
                        else:
                            existing["signals"]["detail_hints_truncated"] = True
                if evidence.get("detail_hints_truncated"):
                    existing["signals"]["detail_hints_truncated"] = True

    def result(self):
        return list(self.rows.values())


def _json_loads(value):
    def constant(_):
        raise ParserError("Non-finite JSON constants are unsupported")
    def pairs(items):
        result={}
        for key,item in items:
            if key in result:
                raise ParserError("Duplicate JSON keys make the evidence ambiguous")
            result[key]=item
        return result
    try:
        return json.loads(value,parse_constant=constant,object_pairs_hook=pairs)
    except RecursionError:
        raise ParserLimitError("JSON nesting exceeded parser capacity") from None
    except (json.JSONDecodeError,UnicodeError):
        raise ParserError("Malformed JSON document") from None


def _schema_types(value):
    if isinstance(value,str):
        value=[value]
    return [item.rsplit("/",1)[-1].rsplit("#",1)[-1] for item in value if isinstance(item,str)] if isinstance(value,list) else []


def _schema_node(node,collector,selector):
    types=_schema_types(node.get("@type",[]))
    dealers=set(types)&{"AutoDealer","MotorcycleDealer"}
    business=dealers or set(types)&{"LocalBusiness","AutomotiveBusiness","AutoRepair","Organization","Corporation"}
    if not business:
        return False
    locator=node.get("url") or node.get("@id") or collector.base
    if isinstance(locator,dict):
        locator=locator.get("@id")
    signals={"schema_types":types,"claim_status":"unverified"}
    if isinstance(node.get("@id"),str):
        try:
            signals["schema_subject"]=normalize_url(node["@id"],collector.base)
        except ValueError:
            # An unsafe optional subject must not leak into evidence/exports.
            # A separate safe URL can still identify the unverified business.
            pass
    address=node.get("address")
    if isinstance(address,dict):
        # These are published address claims, never market or legal domicile.
        fields={key:address[key] for key in ("streetAddress","addressLocality","addressRegion","postalCode","addressCountry") if isinstance(address.get(key),str)}
        if fields:
            signals["published_address"]=fields
    collector.add(locator,"professional_seller" if dealers else "unknown","organization",node.get("name",""),signals,selector,"schema_org_type")
    if dealers and isinstance(address,dict) and isinstance(address.get("streetAddress"),str) and address["streetAddress"].strip():
        collector.add(locator,"point_of_sale","organization",node.get("name",""),signals,selector+".address","schema_org_address")
    return True


def _row_node(node,collector,selector):
    if "tags" in node and isinstance(node["tags"],dict):
        tags=node["tags"]
        locator=tags.get("website") or tags.get("contact:website") or tags.get("url")
        shop=tags.get("shop")
        kind="professional_seller" if shop in ("car","motorcycle") else "unknown"
        signals={"osm_tags":{key:tags[key] for key in ("shop","craft","amenity","name","addr:street","addr:housenumber","addr:city","addr:postcode","addr:country") if isinstance(tags.get(key),str)},"claim_status":"unverified"}
        if node.get("type") in ("node","way","relation") and type(node.get("id")) is int and node["id"] > 0:
            signals["osm_element"]=f"{node['type']}/{node['id']}"
        try:
            locator = normalize_url(locator) if isinstance(locator, str) and locator else None
        except ValueError:
            locator = None
            signals["website_status"] = "published_locator_rejected"
        if not locator and signals.get("osm_element"):
            locator = "https://www.openstreetmap.org/" + signals["osm_element"]
            signals.setdefault("website_status", "not_published")
            signals.update(locator_role="registry_record", discovery_fetch_allowed=False)
        elif locator:
            signals.update(website_status="published_unverified", locator_role="published_website")
        collector.add(locator,kind,"organization",tags.get("name",""),signals,selector,"osm_tags")
        coordinates = node if "lat" in node and "lon" in node else node.get("center", {})
        if kind=="professional_seller" and isinstance(coordinates, dict) and all(type(coordinates.get(key)) in (int,float) for key in ("lat","lon")) and -90<=coordinates["lat"]<=90 and -180<=coordinates["lon"]<=180:
            signals["published_coordinates"]={"latitude":coordinates["lat"],"longitude":coordinates["lon"]}
            collector.add(locator,"point_of_sale","organization",tags.get("name",""),signals,selector,"osm_coordinates")
        return True
    locator=node.get("url") or node.get("website") or node.get("locator")
    if not isinstance(locator,str):
        return False
    kinds=_schema_types(node.get("@type",[]))
    if set(kinds)&{"Car","Vehicle","Motorcycle","Product","Offer","ImageObject"}:
        return True
    kind=node.get("kind","unknown")
    if kind not in _KINDS:
        kind="unknown"
    signals={"claim_status":"unverified"}
    if "timestamp" in node:
        signals["archive_timestamp"]=str(node["timestamp"])
    if "status" in node:
        signals["observed_status"]=str(node["status"])
    for key in ("country","vehicle_classes","source_type"):
        if key in node and isinstance(node[key],(str,list)):
            signals["claimed_"+key]=node[key]
    method="common_crawl_index" if "timestamp" in node and ("filename" in node or "status" in node) else "json_row"
    collector.add(locator,kind,"outbound_link",node.get("name") or node.get("label", ""),signals,selector,method)
    return True


def _walk_json(value,collector,selector="$",depth=0):
    if depth>MAX_DEPTH:
        raise ParserLimitError("JSON nesting limit exceeded")
    collector.count()
    if isinstance(value,dict):
        handled=_schema_node(value,collector,selector)
        if not handled:
            _row_node(value,collector,selector)
        for key,item in value.items():
            if isinstance(item,(list,dict)):
                _walk_json(item,collector,selector+"."+key,depth+1)
    elif isinstance(value,list):
        for index,item in enumerate(value):
            _walk_json(item,collector,f"{selector}[{index}]",depth+1)


class _HTMLDiscovery(HTMLParser):
    def __init__(self,collector):
        super().__init__(convert_charrefs=True)
        self.collector=collector
        self.anchor=None
        self.script=None
        self.links=[]
        self.listings=set()
        self.text=[]
        self.headings=[]
        self.in_heading=False
        self.password=False
        self.json_index=0
        self.suppressed=0

    def handle_starttag(self,tag,attributes):
        self.collector.count()
        attrs=dict(attributes)
        if tag in ("h1","title"):
            self.in_heading=True
        if tag=="script":
            self.script=[] if (attrs.get("type") or "").lower()=="application/ld+json" else None
            self.suppressed+=1
        elif tag=="style":
            self.suppressed+=1
        elif tag=="a" and isinstance(attrs.get("href"),str):
            self._finish_anchor()
            self.anchor={"url":attrs["href"],"text":[],"selector":f"a[{len(self.links)+1}]"}
        elif tag=="input" and (attrs.get("type") or "").lower()=="password":
            self.password=True

    def handle_startendtag(self,tag,attributes):
        self.handle_starttag(tag,attributes)
        self.handle_endtag(tag)

    def handle_endtag(self,tag):
        if tag in ("h1","title"):
            self.in_heading=False
        if tag=="a":
            self._finish_anchor()
        elif tag=="script":
            if self.script is not None:
                self.json_index+=1
                _walk_json(_json_loads("".join(self.script)),self.collector,f"script[type=application/ld+json][{self.json_index}]")
                self.script=None
            self.suppressed=max(0,self.suppressed-1)
        elif tag=="style":
            self.suppressed=max(0,self.suppressed-1)

    def handle_data(self,data):
        if self.script is not None:
            self.script.append(data)
        elif not self.suppressed:
            self.text.append(data)
            if self.in_heading:
                self.headings.append(data)
            if self.anchor:
                self.anchor["text"].append(data)

    def _finish_anchor(self):
        if self.anchor:
            self.links.append(self.anchor)
            self.anchor=None

    def finish(self):
        self.close()
        if self.script is not None:
            raise ParserError("Unclosed JSON-LD script")
        self._finish_anchor()
        text=" ".join(self.text).lower()
        parts_page=bool(_PARTS.search(" ".join(self.headings)))
        state=None
        if self.password or any(value in text for value in ("login required","sign in to continue","iniciar sesión para","connexion requise")):
            state="login_required"
        elif any(value in text for value in ("access denied","verify you are human","captcha","accès refusé","zugriff verweigert")):
            state="restricted"
        elif any(value in text for value in ("permanently closed","cessation d’activité","dauerhaft geschlossen","cerrado permanentemente")):
            state="closed_claim"
        for link in self.links:
            try:
                locator=normalize_url(link["url"],self.collector.base)
            except ValueError:
                continue
            label=" ".join(link["text"])
            path=unquote(urlsplit(locator).path)
            if path.lower().endswith(_MEDIA_SUFFIXES):
                continue
            same_origin=urlsplit(locator).netloc==urlsplit(self.collector.base).netloc
            parts=bool(_PARTS.search(path+" "+label))
            if parts_page and not re.search(r"complete vehicles|véhicules complets|vehículos completos|komplette fahrzeuge",label,re.I):
                parts=True
            if not parts and same_origin and _LISTING.search(path):
                self.listings.add(locator)
                continue
            inventory=not state and not parts and bool(_INVENTORY.search(path+" "+label))
            self.collector.add(locator,"source" if inventory else "unknown","inventory_surface" if inventory else "outbound_link",label,{"claim_status":"unverified","link_context":"parts" if parts else "discovery"},link["selector"],"html_link")
        if state:
            self.collector.add(self.collector.base,"unknown","outbound_link","",{"access_state":state,"inventory_evidence":"unknown"},"document","access_text_or_form")
        elif self.listings:
            self.collector.add(self.collector.base,"source","inventory_surface","",{"listing_link_count":len(self.listings),"claim_status":"unverified","inventory_evidence":"detail_link_hints"},"a[href]","listing_link_pattern")


def _xml_document(text,collector):
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)",text,re.I):
        raise ParserError("XML DTDs and entities are forbidden")
    try:
        root=ET.fromstring(text)
    except (ET.ParseError,ValueError):
        raise ParserError("Malformed XML document") from None
    name=root.tag.rsplit("}",1)[-1]
    if name not in ("urlset","sitemapindex"):
        raise ParserError("XML is not a supported sitemap")
    stack=[(root,0)]
    while stack:
        node,depth=stack.pop()
        collector.count()
        if depth>MAX_DEPTH:
            raise ParserLimitError("XML nesting limit exceeded")
        stack.extend((child,depth+1) for child in reversed(list(node)))
    child_name="sitemap" if name=="sitemapindex" else "url"
    for index,child in enumerate(root):
        if child.tag.rsplit("}",1)[-1]!=child_name:
            continue
        for field in child:
            if field.tag.rsplit("}",1)[-1]=="loc" and field.text:
                collector.add(field.text.strip(),"unknown","sitemap_page","",{"sitemap_index":name=="sitemapindex","claim_status":"unverified"},f"/{name}/{child_name}[{index+1}]/loc","sitemap_locator")


def parse_document(body: bytes,content_type: str,base_url: str) -> list[dict]:
    """Extract candidate claims or raise an explicit parse/limit failure.

An empty result means no candidate hints were found in this document. It never
means zero stock, business closure, country absence or complete discovery.
"""
    if not isinstance(body,bytes):
        raise ParserError("Replay input must be retained bytes")
    if len(body)>MAX_DOCUMENT_BYTES:
        raise ParserLimitError("Document byte budget exceeded")
    if not body.strip():
        raise ParserError("Empty document cannot establish discovery evidence")
    if not isinstance(content_type,str):
        raise ParserError("Content type is required")
    mime=content_type.split(";",1)[0].strip().lower()
    supported={"text/html","application/xhtml+xml","application/xml","text/xml","application/json","application/ld+json","application/x-ndjson","application/jsonl","application/jsonlines","text/plain"}
    if mime not in supported:
        raise ParserError("Unsupported discovery document content type")
    charset=re.search(r"charset\s*=\s*[\"']?([\w-]+)",content_type,re.I)
    encoding=charset.group(1) if charset else "utf-8-sig"
    if encoding.lower() not in {"utf-8","utf8","utf-8-sig","iso-8859-1","latin-1","windows-1252","cp1252"}:
        raise ParserError("Unsupported document character encoding")
    try:
        text=body.decode(encoding)
    except UnicodeError:
        raise ParserError("Document encoding is invalid") from None
    if "\x00" in text:
        raise ParserError("Binary content cannot be replayed as text")
    collector=_Collector(base_url)
    stripped=text.lstrip()
    if mime in ("application/x-ndjson","application/jsonl","application/jsonlines"):
        for index,line in enumerate(text.splitlines()):
            if line.strip():
                _walk_json(_json_loads(line),collector,f"line[{index+1}]")
    elif mime in ("application/json","application/ld+json") or (mime=="text/plain" and stripped.startswith(("{","["))):
        _walk_json(_json_loads(text),collector)
    elif mime in ("application/xml","text/xml") or (mime=="text/plain" and stripped.startswith("<?xml")):
        _xml_document(text,collector)
    elif mime in ("text/html","application/xhtml+xml") or (mime=="text/plain" and stripped.startswith("<")):
        parser=_HTMLDiscovery(collector)
        parser.feed(text)
        parser.finish()
    else:
        raise ParserError("Plain text is not a recognized structured discovery document")
    return collector.result()
