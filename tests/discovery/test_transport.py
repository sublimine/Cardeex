"""Offline transport boundary tests; no remote requests."""
import gzip
import importlib
import io
import socket
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch


class TransportTests(unittest.TestCase):
    def setUp(self):
        try:
            self.t = importlib.import_module("discovery.transport")
        except ModuleNotFoundError:
            self.fail("The governed discovery transport has not been implemented")

    def policy(self, **updates):
        values = dict(allowed_hosts=("dealer.example",),
                      expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
                      operator="test operator", policy_ref="fixture-policy", min_interval_seconds=0)
        values.update(updates)
        return self.t.AccessPolicy(**values)

    def response(self, url, status=200, body=b"ok", **headers):
        return self.t.FetchResult(url, status, headers, body)

    def dns(self, host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", port))]

    def wire_response(self,wire,limit=100):
        class WireSocket:
            def settimeout(self,value): pass
            def connect(self,address): pass
            def sendall(self,value): pass
            def makefile(self,*args): return io.BytesIO(wire)
            def close(self): pass
        with patch.object(self.t.socket,"socket",return_value=WireSocket()):
            return self.t._request("http://dealer.example/","93.184.216.34",1,limit)

    def run_fetch(self, response, policy=None, url="https://dealer.example/stock"):
        def request(target, address, timeout, limit):
            if target.endswith("/robots.txt"):
                return self.response(target, body=b"User-agent: *\nDisallow: /private\n")
            return response(target) if callable(response) else response
        with patch.object(self.t.socket, "getaddrinfo", self.dns), patch.object(self.t, "_request", request):
            return self.t.SafeFetcher(policy or self.policy()).fetch(url)

    def test_url_preserves_facets_and_normalizes_unicode(self):
        url = self.t.normalize_url("/véhicules?class=car&class=lcv&page=2#top", "https://BÜCHER.example/")
        self.assertEqual(url, "https://xn--bcher-kva.example/v%C3%A9hicules?class=car&class=lcv&page=2#top")
        self.assertNotEqual(url, self.t.normalize_url("/véhicules?class=car&page=1", "https://bücher.example"))

    def test_unsafe_url_forms_rejected(self):
        for url in ("file:///etc/passwd", "https://u:p@dealer.example", "https://127.0.0.1/", "https://[::1]/", "https://dealer.example:444/", "https://dealer.example:0/", "https://dealer.example/?access_token=x", "https://dealer.example/?%61pi_key=x", "https://dealer.example/#/route?token=x", "https://dealer.example/#access_token=x", "https://dealer.example/\nprivate", "https://dealer.example\\@evil.example/"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.t.normalize_url(url)

    def test_spa_fragment_routes_are_distinct_observed_locators(self):
        one=self.t.normalize_url("https://dealer.example/#/dealer/a")
        two=self.t.normalize_url("https://dealer.example/#/dealer/b")
        self.assertNotEqual(one,two)
        self.assertTrue(one.endswith("#/dealer/a"))

    def test_authorization_expiry_and_unknown_hosts_fail_before_network(self):
        with patch.object(self.t.socket, "getaddrinfo", side_effect=AssertionError("unexpected DNS")):
            for policy, url in ((self.policy(), "https://other.example/"), (self.policy(expires_at=datetime.now(timezone.utc)-timedelta(seconds=1)), "https://dealer.example/")):
                with self.assertRaises(self.t.PolicyError):
                    self.t.SafeFetcher(policy).fetch(url)
        for updates in (dict(operator=""), dict(policy_ref=""), dict(max_requests=0), dict(timeout_seconds=float("nan")), dict(expires_at="2030-01-01"), dict(allowed_hosts=("*.example",))):
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                self.policy(**updates)

    def test_private_dns_answer_and_mixed_answers_are_blocked(self):
        for addresses in (("127.0.0.1",), ("169.254.169.254",), ("93.184.216.34", "10.0.0.2"), ("::ffff:127.0.0.1",), ("192.0.0.8",), ("192.88.99.1",), ("64:ff9b:1::7f00:1",), ("3fff::1",), ("fec0::1",), ("2606:4700::1111%eth0",)):
            answers = [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip,443)) for ip in addresses]
            with patch.object(self.t.socket,"getaddrinfo",return_value=answers), patch.object(self.t,"_request",side_effect=AssertionError("unexpected request")):
                with self.assertRaises(self.t.PolicyError):
                    self.t.SafeFetcher(self.policy()).fetch("https://dealer.example/")

    def test_dns_resolution_has_a_deadline_without_late_connection(self):
        release=threading.Event()
        def stalled(*args,**kwargs):
            release.wait(2)
            return self.dns("",443)
        start=time.monotonic()
        try:
            with patch.object(self.t.socket,"getaddrinfo",stalled),patch.object(self.t,"_request",side_effect=AssertionError("No late request")):
                with self.assertRaises(self.t.TransportError):
                    self.t.SafeFetcher(self.policy(timeout_seconds=0.1)).fetch("https://dealer.example/")
                self.assertLess(time.monotonic()-start,1)
        finally:
            release.set()

    def test_resolved_address_is_pinned_and_dns_rechecked(self):
        calls=[]
        def request(url, address, timeout, limit):
            calls.append((url,address))
            return self.response(url,body=b"User-agent: *\nAllow: /" if url.endswith("robots.txt") else b"stock")
        with patch.object(self.t.socket,"getaddrinfo",side_effect=[self.dns("",443),self.dns("",443)]) as resolver, patch.object(self.t,"_request",request):
            result=self.t.SafeFetcher(self.policy()).fetch("https://dealer.example/stock")
        self.assertEqual(result.body,b"stock")
        self.assertEqual(resolver.call_count,2)
        self.assertEqual([a for _,a in calls],["93.184.216.34"]*2)

    def test_robots_failure_denial_and_404(self):
        for status in (401,403,500,503):
            with patch.object(self.t.socket,"getaddrinfo",self.dns), patch.object(self.t,"_request",lambda url,*args:self.response(url,status)):
                with self.assertRaises(self.t.RobotsDenied):
                    self.t.SafeFetcher(self.policy()).fetch("https://dealer.example/")
        with self.assertRaises(self.t.RobotsDenied):
            self.run_fetch(None,url="https://dealer.example/private")
        with patch.object(self.t.socket,"getaddrinfo",self.dns), patch.object(self.t,"_request",lambda url,*args:self.response(url,404 if url.endswith("robots.txt") else 200)):
            self.assertEqual(self.t.SafeFetcher(self.policy()).fetch("https://dealer.example/").status,200)

    def test_robots_longest_match_wildcards_groups_and_percent_encoding(self):
        robots=b"User-agent: *\nDisallow: /\nUser-agent: CardeexDiscovery\nDisallow: /stock/*\nAllow: /stock/public\nUser-agent: CardeexDiscovery\nDisallow: /caf%C3%A9\nDisallow: /exact$\n"
        def request(url,*args):
            return self.response(url,body=robots if url.endswith("robots.txt") else b"ok")
        with patch.object(self.t.socket,"getaddrinfo",self.dns), patch.object(self.t,"_request",request):
            fetcher=self.t.SafeFetcher(self.policy())
            for path in ("/stock/private", "/café", "/exact"):
                with self.subTest(path=path),self.assertRaises(self.t.RobotsDenied):
                    fetcher.fetch("https://dealer.example"+path)
            for path in ("/stock/public/three", "/other", "/exactly"):
                self.assertEqual(fetcher.fetch("https://dealer.example"+path).status,200)

    def test_redirect_admission_and_quota(self):
        with self.assertRaises(self.t.PolicyError):
            self.run_fetch(lambda url:self.response(url,302,location="https://other.example/stock"))
        with self.assertRaises(self.t.FetchLimitError):
            self.run_fetch(lambda url:self.response(url,302,location="/next"),self.policy(max_redirects=1))
        with self.assertRaises(self.t.FetchLimitError):
            self.run_fetch(lambda url:self.response(url),self.policy(max_requests=1))

    def test_429_exposes_retry_after_and_stops_host(self):
        with self.assertRaises(self.t.RateLimited) as caught:
            self.run_fetch(lambda url:self.response(url,429,**{"retry-after":"120"}))
        self.assertEqual(caught.exception.retry_after,"120")
        self.assertEqual(caught.exception.url,"https://dealer.example/stock")

    def test_robots_empty_user_agent_cannot_override_wildcard_denial(self):
        rules=self.t._RobotsRules(b"User-agent:\nAllow: /\nUser-agent: *\nDisallow: /\n")
        self.assertFalse(rules.allows("https://dealer.example/stock"))

    def test_429_headers_are_exposed_without_reading_error_body(self):
        result=self.wire_response(b"HTTP/1.1 429 Too Many Requests\r\nContent-Length: 999999\r\nRetry-After: 60\r\n\r\n")
        self.assertEqual(result.status,429)
        self.assertEqual(result.headers["retry-after"],"60")
        self.assertEqual(result.body,b"")

    def test_ambiguous_http_framing_is_rejected(self):
        for framing,body in ((b"Content-Length: 2\r\nContent-Length: 3\r\n",b"okx"),(b"Content-Length: 2\r\nTransfer-Encoding: chunked\r\n",b"2\r\nok\r\n0\r\n\r\n")):
            with self.subTest(framing=framing),self.assertRaises(self.t.TransportError):
                self.wire_response(b"HTTP/1.1 200 OK\r\n"+framing+b"\r\n"+body)

    def test_http_requires_explicit_opt_in(self):
        with self.assertRaises(self.t.PolicyError):
            self.run_fetch(None,url="http://dealer.example/stock")
        result=self.run_fetch(lambda url:self.response(url),self.policy(allow_http=True),"http://dealer.example/stock")
        self.assertEqual(result.status,200)

    def test_durable_hook_charges_robots_redirect_and_failed_attempt(self):
        charged=[]
        def request(url,*args):
            if url.endswith("robots.txt"):
                return self.response(url,404)
            if url.endswith("/stock"):
                return self.response(url,302,location="/next")
            raise self.t.TransportError("connection failed")
        with patch.object(self.t.socket,"getaddrinfo",self.dns),patch.object(self.t,"_request",request):
            fetcher=self.t.SafeFetcher(self.policy(),before_request=charged.append)
            with self.assertRaises(self.t.TransportError):
                fetcher.fetch("https://dealer.example/stock")
        self.assertEqual(charged,["https://dealer.example/robots.txt","https://dealer.example/stock","https://dealer.example/next"])
        self.assertEqual(fetcher.requests_made,3)

    def test_durable_hook_failure_is_preserved_and_prevents_access(self):
        class BudgetDenied(Exception): pass
        def charge(url): raise BudgetDenied("durable budget exhausted")
        with patch.object(self.t.socket,"getaddrinfo",self.dns),patch.object(self.t,"_request",side_effect=AssertionError("No request allowed")):
            with self.assertRaises(BudgetDenied):
                self.t.SafeFetcher(self.policy(),before_request=charge).fetch("https://dealer.example/stock")

    def test_durable_hook_value_error_is_not_rewritten_as_robots(self):
        error=ValueError("registry admission withdrawn")
        def charge(url): raise error
        with patch.object(self.t.socket,"getaddrinfo",self.dns),patch.object(self.t,"_request",side_effect=AssertionError("No request allowed")):
            with self.assertRaises(ValueError) as caught:
                self.t.SafeFetcher(self.policy(),before_request=charge).fetch("https://dealer.example/stock")
        self.assertIs(caught.exception,error)

    def test_dns_rebinding_after_robots_and_redirected_private_literal_blocked(self):
        private=[(socket.AF_INET,socket.SOCK_STREAM,socket.IPPROTO_TCP,"",("192.168.1.1",443))]
        with patch.object(self.t.socket,"getaddrinfo",side_effect=[self.dns("",443),private]),patch.object(self.t,"_request",lambda url,*args:self.response(url,404)) as request:
            with self.assertRaises(self.t.PolicyError):
                self.t.SafeFetcher(self.policy()).fetch("https://dealer.example/stock")
        with self.assertRaises(self.t.PolicyError):
            self.run_fetch(lambda url:self.response(url,302,location="https://127.0.0.1/"))

    def test_429_circuit_remains_closed_without_retry_or_wait(self):
        attempts=[]
        def request(url,*args):
            attempts.append(url)
            return self.response(url,404 if url.endswith("robots.txt") else 429,**{"retry-after":"120"})
        with patch.object(self.t.socket,"getaddrinfo",self.dns),patch.object(self.t,"_request",request):
            fetcher=self.t.SafeFetcher(self.policy())
            for _ in range(2):
                with self.assertRaises(self.t.RateLimited):
                    fetcher.fetch("https://dealer.example/stock")
        self.assertEqual(len(attempts),2)

    def test_robots_garbage_and_restricted_html_are_fail_closed(self):
        for body in (b"upstream temporarily unavailable",b"<html>Please log in</html>"):
            with patch.object(self.t.socket,"getaddrinfo",self.dns),patch.object(self.t,"_request",lambda url,*args:self.response(url,body=body)):
                with self.assertRaises(self.t.RobotsDenied):
                    self.t.SafeFetcher(self.policy()).fetch("https://dealer.example/stock")

    def test_robots_greatest_specific_agent_group_wins(self):
        rules=self.t._RobotsRules(b"User-agent: Cardeex\nDisallow: /\nUser-agent: CardeexDiscovery\nAllow: /stock\n")
        self.assertTrue(rules.allows("https://dealer.example/stock"))
        self.assertTrue(rules.allows("https://dealer.example/other"))

    def test_robots_literal_question_mark_and_reserved_percent_octets(self):
        rules=self.t._RobotsRules(b"User-agent: *\nDisallow: /stock?private=1\nDisallow: /a%2Fb\nDisallow: /%70rivate\n")
        self.assertFalse(rules.allows("https://dealer.example/stock?private=1"))
        self.assertTrue(rules.allows("https://dealer.example/stockXprivate=1"))
        self.assertFalse(rules.allows("https://dealer.example/a%2fb"))
        self.assertTrue(rules.allows("https://dealer.example/a/b"))
        self.assertFalse(rules.allows("https://dealer.example/private"))

    def test_robots_rfc9309_literal_star_and_dollar_match_encoded_rules(self):
        for rule,path in (("/path/file-with-a-%2A.html","/path/file-with-a-*.html"),
                          ("/path/foo-%24","/path/foo-$"),
                          ("/path/foo-$bar","/path/foo-$bar")):
            with self.subTest(rule=rule):
                rules=self.t._RobotsRules(f"User-agent: *\nDisallow: {rule}\n".encode())
                self.assertFalse(rules.allows("https://dealer.example"+path))

    def test_per_host_throttle_applies_to_robots_and_page(self):
        now=[10.0]
        waits=[]
        def sleep(seconds): waits.append(seconds); now[0]+=seconds
        with patch.object(self.t.time,"monotonic",lambda:now[0]),patch.object(self.t.time,"sleep",sleep),patch.object(self.t.socket,"getaddrinfo",self.dns),patch.object(self.t,"_request",lambda url,*args:self.response(url,404 if url.endswith("robots.txt") else 200)):
            self.t.SafeFetcher(self.policy(min_interval_seconds=2)).fetch("https://dealer.example/stock")
        self.assertEqual(waits,[2.0])

    def test_required_interval_includes_robots_crawl_delay(self):
        def request(url,*args):
            return self.response(url,body=b"User-agent: *\nCrawl-delay: 4\nAllow: /\n" if url.endswith("robots.txt") else b"ok")
        with patch.object(self.t.socket,"getaddrinfo",self.dns),patch.object(self.t,"_request",request),patch.object(self.t.time,"sleep",lambda _:None):
            fetcher=self.t.SafeFetcher(self.policy())
            fetcher.fetch("https://dealer.example/stock")
            self.assertEqual(fetcher.required_interval("https://dealer.example/next"),4)

    def test_aggregate_deadline_rejects_expired_and_excessive_throttle(self):
        with patch.object(self.t.socket,"getaddrinfo",side_effect=AssertionError("No DNS after deadline")):
            with self.assertRaises(self.t.FetchLimitError):
                self.t.SafeFetcher(self.policy(),deadline_at=time.monotonic()-1).fetch("https://dealer.example/stock")
        with patch.object(self.t.socket,"getaddrinfo",self.dns),patch.object(self.t,"_request",lambda url,*args:self.response(url,404)),patch.object(self.t.time,"sleep",side_effect=AssertionError("No wait beyond deadline")):
            with self.assertRaises(self.t.FetchLimitError):
                self.t.SafeFetcher(self.policy(min_interval_seconds=10),deadline_at=time.monotonic()+0.5).fetch("https://dealer.example/stock")

    def test_aggregate_deadline_is_shared_by_dns_robots_and_response(self):
        current=[100.0]
        request_timeouts=[]
        def dns(*args,**kwargs):
            current[0]+=0.1
            return self.dns("",443)
        def request(url,address,timeout,limit):
            request_timeouts.append(timeout)
            current[0]+=0.2
            return self.response(url,404 if url.endswith("robots.txt") else 200)
        with patch.object(self.t.time,"monotonic",lambda:current[0]),patch.object(self.t.socket,"getaddrinfo",dns),patch.object(self.t,"_request",request):
            fetcher=self.t.SafeFetcher(self.policy(),deadline_at=101.0)
            fetcher.fetch("https://dealer.example/stock")
        self.assertAlmostEqual(request_timeouts[0],0.9)
        self.assertAlmostEqual(request_timeouts[1],0.6)

    def test_deadline_validation_and_policy_hook_delay(self):
        for deadline in (True,float("nan"),float("inf"),"soon"):
            with self.subTest(deadline=deadline),self.assertRaises(ValueError):
                self.t.SafeFetcher(self.policy(),deadline_at=deadline)
        current=[100.0]
        def charge(url): current[0]=102.0
        with patch.object(self.t.time,"monotonic",lambda:current[0]),patch.object(self.t.socket,"getaddrinfo",self.dns),patch.object(self.t,"_request",side_effect=AssertionError("No request after slow policy guard")):
            with self.assertRaises(self.t.FetchLimitError):
                self.t.SafeFetcher(self.policy(),before_request=charge,deadline_at=101.0).fetch("https://dealer.example/stock")

    def test_bounded_decompression_and_unsupported_encoding(self):
        compressed=gzip.compress(b"x"*2000)
        with self.assertRaises(self.t.FetchLimitError):
            self.t._decode_body(compressed,{"content-encoding":"gzip"},1000)
        self.assertEqual(self.t._decode_body(gzip.compress(b"hello"),{"content-encoding":"gzip"},100),b"hello")
        with self.assertRaises(self.t.TransportError):
            self.t._decode_body(b"not decoded",{"content-encoding":"br"},100)

    def test_pinned_connection_uses_numeric_socket_and_real_http_parser(self):
        class FakeSocket:
            def __init__(self): self.sent=[]; self.connected=None
            def settimeout(self,value): pass
            def connect(self,address): self.connected=address
            def sendall(self,value): self.sent.append(value)
            def makefile(self,*args): return io.BytesIO(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
            def close(self): pass
        fake=FakeSocket()
        with patch.object(self.t.socket,"socket",return_value=fake), patch.object(self.t.socket,"getaddrinfo",side_effect=AssertionError("DNS must not repeat inside socket")):
            result=self.t._request("http://dealer.example/stock?class=car#/dealer/a","93.184.216.34",3,100)
        self.assertEqual(fake.connected,("93.184.216.34",80))
        self.assertEqual(result.body,b"ok")
        self.assertIn(b"Host: dealer.example",b"".join(fake.sent))
        self.assertNotIn(b"#/dealer/a",b"".join(fake.sent))

    def test_tls_retains_hostname_verification_and_sni_on_pinned_socket(self):
        class FakeSocket:
            def settimeout(self,value): pass
            def connect(self,address): self.connected=address
            def close(self): pass
        class TLSContext:
            def wrap_socket(self,sock,server_hostname):
                self.hostname=server_hostname
                return sock
        context=TLSContext()
        fake=FakeSocket()
        with patch.object(self.t.socket,"socket",return_value=fake),patch.object(self.t.ssl,"create_default_context",return_value=context):
            connection=self.t._PinnedHTTPSConnection("dealer.example","93.184.216.34",443,3)
            connection.connect()
            connection.close()
        self.assertEqual(fake.connected,("93.184.216.34",443))
        self.assertEqual(context.hostname,"dealer.example")

    def test_total_response_deadline_interrupts_slow_header_stream(self):
        interrupted=threading.Event()
        class SlowFile:
            def readline(self,*args):
                interrupted.wait(2)
                raise OSError("socket shut down")
            def close(self): pass
            def flush(self): pass
        class SlowSocket:
            def settimeout(self,value): pass
            def connect(self,address): pass
            def sendall(self,value): pass
            def makefile(self,*args): return SlowFile()
            def shutdown(self,how): interrupted.set()
            def close(self): pass
        start=time.monotonic()
        with patch.object(self.t.socket,"socket",return_value=SlowSocket()):
            with self.assertRaises(self.t.TransportError):
                self.t._request("http://dealer.example/","93.184.216.34",0.1,100)
        self.assertTrue(interrupted.is_set())
        self.assertLess(time.monotonic()-start,1)

    def test_total_response_deadline_interrupts_connection_close_body(self):
        interrupted=threading.Event()
        class SlowFile:
            def __init__(self): self.headers=io.BytesIO(b"HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: 10\r\n\r\n")
            def readline(self,*args): return self.headers.readline(*args)
            def read(self,*args):
                interrupted.wait(2)
                raise OSError("socket shut down")
            def close(self): pass
            def flush(self): pass
        class SlowSocket:
            def settimeout(self,value): pass
            def connect(self,address): pass
            def sendall(self,value): pass
            def makefile(self,*args): return SlowFile()
            def shutdown(self,how): interrupted.set()
            def close(self): pass
        start=time.monotonic()
        with patch.object(self.t.socket,"socket",return_value=SlowSocket()):
            with self.assertRaises(self.t.TransportError):
                self.t._request("http://dealer.example/","93.184.216.34",0.1,100)
        self.assertTrue(interrupted.is_set())
        self.assertLess(time.monotonic()-start,1)


if __name__ == "__main__":
    unittest.main()
