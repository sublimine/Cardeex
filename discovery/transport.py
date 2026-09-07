"""Bounded discovery transport. Robots permission is never legal authorization.

The caller supplies an independently approved, expiring policy. No hosts are
admitted from document content. Budgets include robots requests and redirects.
Only public HTTP(S), without cookies, credentials or environment proxies, is
supported. HTTPS is mandatory unless the caller explicitly opts into HTTP.
"""
from __future__ import annotations

import fnmatch
import http.client
import ipaddress
import math
import re
import socket
import ssl
import threading
import time
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Callable
from urllib.parse import parse_qsl, quote, unquote, urljoin, urlsplit, urlunsplit

USER_AGENT = "CardeexDiscovery/0.1"
MAX_URL_LENGTH = 8192
_SECRET_NAMES = {"key", "apikey", "accesskey", "token", "accesstoken", "refreshtoken", "idtoken", "auth", "authorization", "password", "passwd", "secret", "signature", "sig", "session", "sessionid", "credential", "credentials", "jwt", "code", "clientsecret", "subscriptionkey"}
_UNRESERVED = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
# Conservative exclusions also cover classifications corrected after Python 3.11.
_SPECIAL_NETWORKS = tuple(ipaddress.ip_network(value) for value in (
    "192.0.0.0/24", "192.88.99.0/24", "64:ff9b::/96", "64:ff9b:1::/48",
    "2001::/23", "2002::/16", "3fff::/20",
))


class TransportError(RuntimeError):
    """Request failed without demonstrating absence or empty inventory."""


class PolicyError(TransportError):
    """Admission or network boundary denied the request."""


class RobotsDenied(PolicyError):
    """Robots denied access or could not be safely evaluated."""


class FetchLimitError(TransportError):
    """A finite request, response or redirect limit was reached."""


class RateLimited(TransportError):
    status = 429

    def __init__(self, retry_after: str | None, url: str | None = None):
        self.retry_after = retry_after
        self.url = url
        super().__init__("Remote rate limit; no automatic retry was performed")


def _public_address(value: str) -> str:
    address = ipaddress.ip_address(value)
    if not address.is_global or address.is_multicast or address.is_reserved or address.is_unspecified or getattr(address,"is_site_local",False) or "%" in value:
        raise ValueError("Only globally routable unicast addresses are permitted")
    if any(address in network for network in _SPECIAL_NETWORKS if address.version==network.version):
        raise ValueError("Special-use address range is not supported")
    if getattr(address, "ipv4_mapped", None):
        _public_address(str(address.ipv4_mapped))
    # Translation/tunnel formats can conceal another destination address.
    if address.version == 6 and (address.sixtofour or address.teredo or address in ipaddress.ip_network("64:ff9b::/96")):
        raise ValueError("IPv6 address translation and tunnelling are not supported")
    return str(address)


def _host(value: str) -> str:
    if not isinstance(value, str) or not value or value.endswith("."):
        raise ValueError("An explicit host without a trailing dot is required")
    try:
        return _public_address(value)
    except ValueError:
        if ":" in value or re.fullmatch(r"[0-9.]+", value):
            raise ValueError("Unsafe IP address") from None
    try:
        result = value.encode("idna").decode("ascii").lower()
    except UnicodeError:
        raise ValueError("Invalid hostname") from None
    if len(result) > 253 or "." not in result or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", part) for part in result.split(".")):
        raise ValueError("Invalid explicit hostname")
    if result.endswith((".localhost", ".local", ".internal", ".home", ".lan")):
        raise ValueError("Private hostname")
    return result


def normalize_url(url: str, base: str | None = None) -> str:
    """Normalize an observed locator, preserving query order and repeated facets.

Observation supports HTTP without silently upgrading it. Fetch policy decides
    whether HTTP access is admitted. Fragments are retained because SPA routes
    can identify distinct observed dealer pages; HTTP request targets omit them.
"""
    if not isinstance(url, str) or not url or len(url) > MAX_URL_LENGTH:
        raise ValueError("Missing or oversized URL")
    if any(ord(char) < 32 or ord(char) == 127 for char in url) or "\\" in url:
        raise ValueError("URL contains forbidden characters")
    url = url.strip()
    if base is not None:
        url = urljoin(normalize_url(base), url)
    parts = urlsplit(url)
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        raise ValueError("Only explicit HTTP(S) locators are supported")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise ValueError("URL credentials are forbidden")
    host = _host(parts.hostname)
    port = parts.port
    expected_port = 443 if parts.scheme.lower() == "https" else 80
    if port is not None and port != expected_port:
        raise ValueError("Nonstandard destination ports are not supported")
    parameters=parse_qsl(parts.query,keep_blank_values=True,max_num_fields=100)
    fragment_parameters=parts.fragment.split("?",1)[-1]
    parameters+=parse_qsl(fragment_parameters,keep_blank_values=True,max_num_fields=100)
    for name, _ in parameters:
        for _ in range(3):
            name = unquote(name)
        normalized = re.sub(r"[^a-z0-9]", "", name.lower())
        if normalized in _SECRET_NAMES or any(word in normalized for word in ("token", "password", "secret", "signature", "credential")) or normalized.startswith("xamz"):
            raise ValueError("Credential-bearing query parameters are forbidden")
    if re.search(r"%(?![0-9a-fA-F]{2})", parts.path + parts.query + parts.fragment):
        raise ValueError("Malformed percent encoding")
    authority = f"[{host}]" if ":" in host else host
    path = quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~")
    query = quote(parts.query, safe="%/?@:!$&'()*+,;=-._~")
    fragment = quote(parts.fragment, safe="%/?@:!$&'()*+,;=-._~")
    result = urlunsplit((parts.scheme.lower(), authority, path, query, fragment))
    if len(result) > MAX_URL_LENGTH:
        raise ValueError("Normalized URL exceeds limit")
    return result


@dataclass(frozen=True)
class AccessPolicy:
    allowed_hosts: tuple[str, ...]
    expires_at: datetime | str
    operator: str
    policy_ref: str
    max_requests: int = 20
    max_bytes: int = 1024 * 1024
    timeout_seconds: float = 15
    min_interval_seconds: float = 1
    purpose: str = "discovery"
    max_redirects: int = 5
    allow_http: bool = False

    def __post_init__(self):
        if not isinstance(self.allowed_hosts, (tuple, list)) or not self.allowed_hosts or len(self.allowed_hosts) > 1000:
            raise ValueError("An explicit bounded host allowlist is required")
        object.__setattr__(self, "allowed_hosts", tuple(dict.fromkeys(_host(h) for h in self.allowed_hosts)))
        expiry = self.expires_at
        if isinstance(expiry, str):
            expiry = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
        if not isinstance(expiry, datetime) or expiry.tzinfo is None or expiry.utcoffset() is None:
            raise ValueError("Expiry must include a timezone")
        object.__setattr__(self, "expires_at", expiry.astimezone(timezone.utc))
        if not all(isinstance(v, str) and v.strip() and len(v) <= 512 for v in (self.operator, self.policy_ref)):
            raise ValueError("Operator and independent policy reference are required")
        if self.purpose != "discovery" or not isinstance(self.allow_http, bool):
            raise ValueError("Only discovery policies are supported")
        for value, lower, upper in ((self.max_requests,1,10000),(self.max_bytes,1,8*1024*1024),(self.max_redirects,0,10)):
            if type(value) is not int or not lower <= value <= upper:
                raise ValueError("Request, byte and redirect budgets must be finite bounded integers")
        for value, lower, upper in ((self.timeout_seconds,0.1,60),(self.min_interval_seconds,0,60)):
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not lower <= value <= upper:
                raise ValueError("Timing limits must be finite and bounded")


@dataclass(frozen=True)
class FetchResult:
    url: str
    status: int
    headers: dict[str, str]
    body: bytes


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host, address, port, timeout):
        super().__init__(host, port=port, timeout=timeout)
        self._address = address

    def connect(self):
        family = socket.AF_INET6 if ":" in self._address else socket.AF_INET
        sock = socket.socket(family, socket.SOCK_STREAM)
        try:
            sock.settimeout(self.timeout)
            sock.connect((self._address, self.port))
        except BaseException:
            sock.close()
            raise
        self.sock = sock


class _PinnedHTTPSConnection(_PinnedHTTPConnection):
    def connect(self):
        super().connect()
        try:
            self.sock = ssl.create_default_context().wrap_socket(self.sock, server_hostname=self.host)
        except BaseException:
            self.close()
            raise


def _decode_body(body: bytes, headers: dict[str, str], limit: int) -> bytes:
    if len(body) > limit:
        raise FetchLimitError("Encoded response exceeded byte budget")
    encoding = headers.get("content-encoding", "identity").strip().lower()
    if encoding in ("", "identity"):
        return body
    if encoding not in ("gzip", "deflate"):
        raise TransportError("Unsupported response content encoding")
    try:
        decoder = zlib.decompressobj(16 + zlib.MAX_WBITS if encoding == "gzip" else zlib.MAX_WBITS)
        result = decoder.decompress(body, limit + 1)
        if len(result) > limit or decoder.unconsumed_tail:
            raise FetchLimitError("Decoded response exceeded byte budget")
        if not decoder.eof or decoder.unused_data:
            raise TransportError("Incomplete or concatenated compressed response")
        return result
    except zlib.error:
        raise TransportError("Malformed compressed response") from None


def _request(url: str, address: str, timeout: float, limit: int) -> FetchResult:
    """One pinned request; no redirect following, cookies, auth or proxy lookup."""
    parts = urlsplit(url)
    secure = parts.scheme == "https"
    cls = _PinnedHTTPSConnection if secure else _PinnedHTTPConnection
    connection = cls(parts.hostname, address, 443 if secure else 80, timeout)
    expired = threading.Event()
    active_socket = [None]
    def abort():
        expired.set()
        # http.client can clear connection.sock after headers while its response
        # file still owns a socket reference. Keep the transport socket pinned.
        active = active_socket[0] or connection.sock
        if active is not None:
            try:
                active.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()
    timer = threading.Timer(timeout, abort)
    timer.daemon = True
    timer.start()
    try:
        connection.connect()
        active_socket[0] = connection.sock
        if expired.is_set():
            abort()
            raise TransportError("Connection deadline exceeded")
        target = parts.path + ("?" + parts.query if parts.query else "")
        connection.request("GET", target, headers={"User-Agent":USER_AGENT,"Accept":"text/html,application/json,application/xml,text/plain;q=0.8","Accept-Encoding":"identity","Connection":"close"})
        response = connection.getresponse()
        pairs = response.getheaders()
        if sum(len(k)+len(v) for k,v in pairs) > 32768:
            raise FetchLimitError("Response headers exceeded budget")
        names=[key.lower() for key,_ in pairs]
        if any(names.count(key)>1 for key in ("content-length","content-encoding","location","retry-after")):
            raise TransportError("Ambiguous duplicate response headers")
        if "transfer-encoding" in names and "content-length" in names:
            raise TransportError("Ambiguous HTTP response framing")
        allowed = {"content-type","content-length","content-encoding","etag","last-modified","location","retry-after"}
        headers = {key.lower():value for key,value in pairs if key.lower() in allowed}
        if response.status>=300:
            # Headers carry redirects and rate limits; error bodies cannot be
            # discovery evidence and need not consume their advertised bytes.
            return FetchResult(url,response.status,headers,b"")
        if "content-length" in headers:
            try:
                length = int(headers["content-length"])
            except ValueError:
                raise TransportError("Invalid Content-Length") from None
            if length < 0 or length > limit:
                raise FetchLimitError("Declared response exceeded byte budget")
        body = response.read(limit + 1)
        if expired.is_set():
            raise TransportError("Response deadline exceeded")
        if "content-length" in headers and len(body) < int(headers["content-length"]):
            raise TransportError("Truncated response")
        return FetchResult(url,response.status,headers,_decode_body(body,headers,limit))
    except (OSError, http.client.HTTPException) as exc:
        raise TransportError("Network or HTTP transport failed") from exc
    finally:
        timer.cancel()
        connection.close()


def _resolve(host: str, port: int, timeout: float) -> list[str]:
    """Bound DNS latency. An abandoned resolver can never issue an HTTP request."""
    completed=threading.Event()
    outcome=[]
    def resolve():
        try:
            outcome.append(socket.getaddrinfo(host,port,type=socket.SOCK_STREAM,proto=socket.IPPROTO_TCP))
        except Exception as exc:
            outcome.append(exc)
        finally:
            completed.set()
    worker=threading.Thread(target=resolve,name="cardeex-dns",daemon=True)
    worker.start()
    if not completed.wait(timeout):
        raise TransportError("Destination resolution deadline exceeded")
    if isinstance(outcome[0],Exception):
        raise TransportError("Destination resolution failed") from outcome[0]
    try:
        addresses=list(dict.fromkeys(_public_address(item[4][0]) for item in outcome[0]))
        if not addresses:
            raise ValueError("No public destination addresses")
    except ValueError as exc:
        raise PolicyError("DNS returned an unsafe destination") from exc
    return addresses


def _robots_octets(value: str, *, rule: bool = False) -> str:
    value = quote(value, safe="/%?@:!$&'()*+,;=-._~")
    # RFC 9309 Figure 6: these are literal octets in a request URL but
    # wildcard/end-anchor syntax in a rule. Encoded rule octets stay literal.
    if rule:
        end_anchor=value.endswith("$")
        value=value.replace("$","%24")
        if end_anchor:
            value=value[:-3]+"$"
    else:
        value=value.replace("*","%2A").replace("$","%24")
    return re.sub(r"%([0-9a-fA-F]{2})",lambda m:chr(int(m[1],16)) if chr(int(m[1],16)) in _UNRESERVED else "%"+m[1].upper(),value)


class _RobotsRules:
    def __init__(self, body: bytes):
        try:
            content = body.decode("utf-8-sig")
        except UnicodeError:
            raise RobotsDenied("Robots text is not valid UTF-8") from None
        if "\x00" in content or "<html" in content.lower() or "<!doctype" in content.lower():
            raise RobotsDenied("Robots response is not a rules document")
        groups=[]
        agents=[]
        rules=[]
        delay=0.0
        seen_rules=False
        recognized=False
        noncomment=False
        for line_index,line in enumerate(content.splitlines()):
            if line_index>10000:
                raise RobotsDenied("Robots line budget exceeded")
            line=line.split("#",1)[0].strip()
            noncomment=noncomment or bool(line)
            if not line or ":" not in line:
                continue
            key,value=(part.strip() for part in line.split(":",1))
            key=key.lower()
            recognized=recognized or key in ("user-agent","allow","disallow","sitemap","crawl-delay")
            if key=="user-agent":
                if not value:
                    continue
                if seen_rules:
                    groups.append((agents,rules,delay))
                    agents,rules,delay,seen_rules=[],[],0.0,False
                agents.append(value.lower())
            elif key in ("allow","disallow") and agents:
                seen_rules=True
                if value:
                    if len(value)>MAX_URL_LENGTH:
                        raise RobotsDenied("Robots rule exceeds parsing budget")
                    rules.append((key=="allow",_robots_octets(value,rule=True)))
            elif key=="crawl-delay" and agents:
                seen_rules=True
                try:
                    delay=max(delay,float(value))
                except ValueError:
                    raise RobotsDenied("Invalid crawl delay") from None
                if not math.isfinite(delay) or not 0 <= delay <= 60:
                    raise RobotsDenied("Crawl delay exceeds supported timing budget")
            if len(rules)+sum(len(group[1]) for group in groups)>5000:
                raise RobotsDenied("Robots rules exceed parsing budget")
        groups.append((agents,rules,delay))
        if noncomment and not recognized:
            raise RobotsDenied("Robots response contains no recognizable directives")
        token=USER_AGENT.split("/",1)[0].lower()
        specific=[g for g in groups if any(a!="*" and a in token for a in g[0])]
        if specific:
            longest=max(len(a) for g in specific for a in g[0] if a!="*" and a in token)
            specific=[g for g in specific if any(len(a)==longest and a in token for a in g[0])]
        selected=specific or [g for g in groups if "*" in g[0]]
        self.rules=[rule for _,group_rules,_ in selected for rule in group_rules]
        self.delay=max((d for _,_,d in selected),default=0.0)

    def allows(self,url: str) -> bool:
        parts=urlsplit(url)
        target=_robots_octets(parts.path+("?"+parts.query if parts.query else ""))
        matches=[]
        for allowed,rule in self.rules:
            pattern=rule[:-1] if rule.endswith("$") else rule+"*"
            # fnmatch's translated wildcards use atomic groups on Python 3.11+,
            # avoiding exponential backtracking on hostile repeated stars.
            pattern=pattern.replace("[","[[]").replace("?","[?]")
            if fnmatch.fnmatchcase(target,pattern):
                matches.append((len(rule.rstrip("$").replace("*","")),allowed))
        return max(matches,default=(0,True))[1]


class SafeFetcher:
    """One finite campaign transport. Instances serialize budget and throttle use."""
    def __init__(self,policy: AccessPolicy, before_request: Callable[[str],None] | None = None,
                 deadline_at: float | None = None):
        if not isinstance(policy,AccessPolicy):
            raise ValueError("An explicit AccessPolicy is required")
        if deadline_at is not None and (isinstance(deadline_at,bool) or not isinstance(deadline_at,(int,float)) or not math.isfinite(deadline_at)):
            raise ValueError("Aggregate deadline must be a finite monotonic clock value")
        self.policy=policy
        self._before_request=before_request
        self.request_phase="document"
        self.deadline_at=deadline_at
        self.requests_made=0
        self.bytes_received=0
        self._last_request={}
        self._robots={}
        self._blocked={}
        self._lock=threading.RLock()

    def _time_left(self) -> float:
        remaining=float("inf") if self.deadline_at is None else self.deadline_at-time.monotonic()
        if remaining<=0:
            raise FetchLimitError("Aggregate discovery deadline exceeded")
        return remaining

    def _stage_timeout(self) -> float:
        return min(self.policy.timeout_seconds,self._time_left())

    def required_interval(self,url: str) -> float:
        """Required host delay for callers implementing durable shared throttles."""
        host=urlsplit(normalize_url(url)).hostname
        return max(self.policy.min_interval_seconds,max((rule.delay for origin,(_,rule) in self._robots.items() if urlsplit(origin).hostname==host),default=0))

    def _admit(self,url: str) -> str:
        self._time_left()
        try:
            url=normalize_url(url)
        except ValueError as exc:
            raise PolicyError("Unsafe discovery URL") from exc
        parts=urlsplit(url)
        if parts.scheme!="https" and not self.policy.allow_http:
            raise PolicyError("HTTP requires an explicit policy opt-in")
        if parts.hostname not in self.policy.allowed_hosts:
            raise PolicyError("Destination host is not explicitly admitted")
        if datetime.now(timezone.utc)>=self.policy.expires_at:
            raise PolicyError("Discovery policy has expired")
        return url

    def _one(self,url: str, *, purpose: str = "document") -> FetchResult:
        url=self._admit(url)
        parts=urlsplit(url)
        host=parts.hostname
        if host in self._blocked:
            until,retry=self._blocked[host]
            if time.monotonic()<until:
                raise RateLimited(retry,url)
        if self.requests_made>=self.policy.max_requests:
            raise FetchLimitError("Campaign request budget exhausted")
        interval=self.required_interval(url)
        wait=interval-(time.monotonic()-self._last_request.get(host,float("-inf")))
        if wait>0:
            if wait>=self._time_left():
                raise FetchLimitError("Required throttle exceeds aggregate deadline")
            time.sleep(wait)
        self._admit(url)
        addresses=_resolve(host,443 if parts.scheme=="https" else 80,self._stage_timeout())
        self._admit(url)
        if self._before_request is not None:
            self.request_phase=purpose
            self._before_request(url)
        self._admit(url)
        self.requests_made+=1
        self._last_request[host]=time.monotonic()
        result=_request(url,addresses[0],self._stage_timeout(),self.policy.max_bytes)
        self._time_left()
        self.bytes_received+=len(result.body)
        if len(result.body)>self.policy.max_bytes:
            raise FetchLimitError("Response exceeded byte budget")
        if result.status==429:
            retry=result.headers.get("retry-after")
            seconds=float("inf")
            try:
                seconds=max(0,float(retry))
                if not math.isfinite(seconds): seconds=float("inf")
            except (TypeError,ValueError):
                try:
                    seconds=max(0,(parsedate_to_datetime(retry)-datetime.now(timezone.utc)).total_seconds())
                except (TypeError,ValueError,OverflowError):
                    pass
            self._blocked[host]=(time.monotonic()+seconds,retry)
            raise RateLimited(retry,url)
        return result

    def _rules(self,url: str) -> _RobotsRules:
        parts=urlsplit(url)
        origin=urlunsplit((parts.scheme,parts.netloc,"","",""))
        cached=self._robots.get(origin)
        if cached and time.monotonic()-cached[0]<86400:
            return cached[1]
        target=origin+"/robots.txt"
        try:
            for hop in range(self.policy.max_redirects+1):
                result=self._one(target,purpose="robots")
                if result.status in (301,302,303,307,308):
                    if hop==self.policy.max_redirects:
                        raise RobotsDenied("Robots redirect limit exceeded")
                    target=self._admit(normalize_url(result.headers.get("location",""),target))
                    continue
                if result.status==404:
                    rules=_RobotsRules(b"")
                elif result.status==200:
                    rules=_RobotsRules(result.body)
                else:
                    raise RobotsDenied("Robots admission unavailable")
                self._robots[origin]=(time.monotonic(),rules)
                return rules
        except (RateLimited,FetchLimitError,PolicyError):
            raise
        except ValueError:
            # Durable-policy hook exceptions retain their type and identity.
            raise
        raise RobotsDenied("Robots admission could not be established")

    def fetch(self,url: str) -> FetchResult:
        with self._lock:
            url=self._admit(url)
            for hop in range(self.policy.max_redirects+1):
                if not self._rules(url).allows(url):
                    raise RobotsDenied("Robots rules deny this discovery locator")
                result=self._one(url)
                if result.status not in (301,302,303,307,308):
                    return result
                if hop==self.policy.max_redirects:
                    raise FetchLimitError("Redirect budget exhausted")
                try:
                    url=self._admit(normalize_url(result.headers.get("location",""),url))
                except ValueError as exc:
                    raise PolicyError("Redirect target is unsafe or missing") from exc
        raise FetchLimitError("Redirect budget exhausted")
