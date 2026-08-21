from __future__ import annotations

import gzip
import ssl
from types import SimpleNamespace
from pathlib import Path

import pytest
import requests
from requests.adapters import BaseAdapter

from Crawler import Crawler_MunicipalYaml
from Crawler import library_usage_info
from utils import outbound_http


PUBLIC_IP = "93.184.216.34"
ROOT = Path(__file__).resolve().parents[1]


class _PeerSocket:
    def __init__(self, address: str) -> None:
        self.address = address

    def getpeername(self):
        return self.address, 443


class _RawResponse:
    def __init__(self, peer: str) -> None:
        self.connection = SimpleNamespace(sock=_PeerSocket(peer))
        self._original_response = None
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _StaticAdapter(BaseAdapter):
    def __init__(self, routes: dict[str, tuple[int, dict[str, str], bytes]], peer: str = PUBLIC_IP) -> None:
        self.routes = routes
        self.peer = peer
        self.requests: list[tuple[str, object]] = []

    def send(self, request, **kwargs):
        self.requests.append((request.url, kwargs.get("timeout")))
        status, headers, body = self.routes[request.url]
        response = requests.Response()
        response.status_code = status
        response.headers.update(headers)
        response.url = request.url
        response.request = request
        response.raw = _RawResponse(self.peer)
        response._content = body
        response._content_consumed = True
        return response

    def close(self) -> None:
        return None


def _public_resolver(hostname: str, _port: int) -> tuple[str, ...]:
    if hostname == "public.example":
        return (PUBLIC_IP,)
    return outbound_http.resolve_public_addresses(hostname, _port)


@pytest.mark.parametrize(
    "url",
    (
        "file:///etc/passwd",
        "https://user:password@public.example/private",
        "http://127.0.0.1/admin",
        "http://[::1]/admin",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.5/internal",
    ),
)
def test_outbound_url_validation_rejects_active_and_non_public_targets(url: str) -> None:
    with pytest.raises(outbound_http.OutboundRequestBlocked):
        outbound_http.validate_outbound_url(url)


def test_outbound_url_validation_rejects_private_dns_answers(monkeypatch) -> None:
    monkeypatch.setattr(
        outbound_http.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (outbound_http.socket.AF_INET, outbound_http.socket.SOCK_STREAM, 6, "", ("10.20.30.40", 80)),
        ],
    )

    with pytest.raises(outbound_http.OutboundRequestBlocked, match="non-public"):
        outbound_http.validate_outbound_url("http://internal.example/path")


def test_outbound_url_validation_rejects_mixed_public_and_private_dns_answers(monkeypatch) -> None:
    monkeypatch.setattr(
        outbound_http.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (outbound_http.socket.AF_INET, outbound_http.socket.SOCK_STREAM, 6, "", (PUBLIC_IP, 443)),
            (outbound_http.socket.AF_INET, outbound_http.socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
        ],
    )

    with pytest.raises(outbound_http.OutboundRequestBlocked, match="non-public"):
        outbound_http.validate_outbound_url("https://mixed.example/path")


def test_outbound_dns_resolution_is_time_bounded(monkeypatch) -> None:
    attempts = 0

    class TimedOutFuture:
        def result(self, *, timeout):
            assert timeout <= 5
            raise outbound_http.FutureTimeoutError

        def cancel(self) -> bool:
            return True

    def submit(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        return TimedOutFuture()

    monkeypatch.setattr(
        outbound_http._dns_executor,  # noqa: SLF001 - exercise the DNS deadline boundary.
        "submit",
        submit,
    )

    with pytest.raises(outbound_http.OutboundRequestBlocked, match="timed out"):
        outbound_http.validate_outbound_url("https://slow-dns.example/path")
    assert attempts == 2


def test_outbound_dns_resolution_retries_one_transient_timeout(monkeypatch) -> None:
    attempts = 0

    class Future:
        def result(self, *, timeout):
            assert timeout <= 5
            if attempts == 1:
                raise outbound_http.FutureTimeoutError
            return [
                (
                    outbound_http.socket.AF_INET,
                    outbound_http.socket.SOCK_STREAM,
                    6,
                    "",
                    (PUBLIC_IP, 443),
                )
            ]

        def cancel(self) -> bool:
            return True

    def submit(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        return Future()

    monkeypatch.setattr(
        outbound_http._dns_executor,  # noqa: SLF001 - exercise transient DNS recovery.
        "submit",
        submit,
    )

    destination = outbound_http.validate_outbound_url(
        "https://transient-dns.example/path"
    )
    assert destination.addresses == (PUBLIC_IP,)
    assert attempts == 2


@pytest.mark.parametrize(
    "redirect_target",
    (
        "http://127.0.0.1/admin",
        "http://169.254.169.254/latest/meta-data/",
    ),
)
def test_safe_session_revalidates_every_redirect_hop(monkeypatch, redirect_target: str) -> None:
    original_resolver = outbound_http.resolve_public_addresses

    def resolver(hostname: str, port: int) -> tuple[str, ...]:
        if hostname == "public.example":
            return (PUBLIC_IP,)
        return original_resolver(hostname, port)

    monkeypatch.setattr(outbound_http, "resolve_public_addresses", resolver)
    session = outbound_http.SafeSession(total_timeout_seconds=10)
    adapter = _StaticAdapter(
        {
            "https://public.example/start": (
                302,
                {"Location": redirect_target},
                b"redirect",
            ),
        }
    )
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    with pytest.raises(outbound_http.OutboundRequestBlocked):
        session.get("https://public.example/start", timeout=5)
    assert [url for url, _timeout in adapter.requests] == ["https://public.example/start"]


def test_safe_session_rejects_public_https_to_http_downgrade_before_second_hop(monkeypatch) -> None:
    monkeypatch.setattr(outbound_http, "resolve_public_addresses", lambda *_args: (PUBLIC_IP,))
    session = outbound_http.SafeSession(total_timeout_seconds=10)
    adapter = _StaticAdapter(
        {
            "https://public.example/start": (
                302,
                {"Location": "http://public.example/plaintext"},
                b"redirect",
            ),
            "http://public.example/plaintext": (200, {}, b"unsafe"),
        }
    )
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    with pytest.raises(outbound_http.OutboundRequestBlocked, match="plaintext redirect"):
        session.get("https://public.example/start", timeout=5)
    assert [url for url, _timeout in adapter.requests] == ["https://public.example/start"]


def test_safe_session_enforces_and_resets_context_request_budget(monkeypatch) -> None:
    monkeypatch.setattr(outbound_http, "resolve_public_addresses", lambda *_args: (PUBLIC_IP,))
    session = outbound_http.SafeSession(total_timeout_seconds=10)
    adapter = _StaticAdapter({"https://public.example/data": (200, {}, b"ok")})
    session.mount("https://", adapter)

    with outbound_http.outbound_request_budget(1):
        assert session.get("https://public.example/data", timeout=5).content == b"ok"
        with pytest.raises(outbound_http.OutboundRequestBlocked, match="budget exhausted"):
            session.get("https://public.example/data", timeout=5)

    assert session.get("https://public.example/data", timeout=5).content == b"ok"
    assert [url for url, _timeout in adapter.requests] == [
        "https://public.example/data",
        "https://public.example/data",
    ]


def test_safe_session_rejects_peer_address_mismatch(monkeypatch) -> None:
    monkeypatch.setattr(outbound_http, "resolve_public_addresses", lambda *_args: (PUBLIC_IP,))
    session = outbound_http.SafeSession(total_timeout_seconds=10)
    adapter = _StaticAdapter(
        {"https://public.example/": (200, {}, b"ok")},
        peer="127.0.0.1",
    )
    session.mount("https://", adapter)

    with pytest.raises(outbound_http.OutboundRequestBlocked, match="peer"):
        session.get("https://public.example/", timeout=5)


def test_safe_session_cannot_disable_tls_verification(monkeypatch) -> None:
    monkeypatch.setattr(outbound_http, "resolve_public_addresses", lambda *_args: (PUBLIC_IP,))
    session = outbound_http.SafeSession(total_timeout_seconds=10)
    session.verify = False
    adapter = _StaticAdapter({"https://public.example/": (200, {}, b"ok")})
    session.mount("https://", adapter)

    with pytest.raises(outbound_http.OutboundRequestBlocked, match="TLS verification"):
        session.get("https://public.example/", timeout=5)
    assert adapter.requests == []


def test_safe_session_rechecks_dns_after_connect_to_detect_rebinding(monkeypatch) -> None:
    answers = iter((PUBLIC_IP, "169.254.169.254"))

    def getaddrinfo(_hostname, port, **_kwargs):
        return [
            (
                outbound_http.socket.AF_INET,
                outbound_http.socket.SOCK_STREAM,
                6,
                "",
                (next(answers), port),
            ),
        ]

    monkeypatch.setattr(outbound_http.socket, "getaddrinfo", getaddrinfo)
    session = outbound_http.SafeSession(total_timeout_seconds=10)
    adapter = _StaticAdapter({"https://public.example/": (200, {}, b"ok")})
    session.mount("https://", adapter)

    with pytest.raises(outbound_http.OutboundRequestBlocked, match="non-public"):
        session.get("https://public.example/", timeout=5)


def test_safe_session_bounds_timeout_and_response_size(monkeypatch) -> None:
    monkeypatch.setattr(outbound_http, "resolve_public_addresses", lambda *_args: (PUBLIC_IP,))
    session = outbound_http.SafeSession(
        max_response_bytes=16,
        total_timeout_seconds=10,
    )
    adapter = _StaticAdapter(
        {
            "https://public.example/ok": (200, {}, b"ok"),
            "https://public.example/large": (200, {"Content-Length": "100"}, b"x" * 100),
        }
    )
    session.mount("https://", adapter)

    response = session.get("https://public.example/ok", timeout=999)
    assert response.content == b"ok"
    assert float(adapter.requests[0][1]) <= 10
    with pytest.raises(outbound_http.OutboundResponseTooLarge):
        session.get("https://public.example/large", timeout=5)


def test_safe_session_security_bounds_cannot_be_disabled() -> None:
    with pytest.raises(ValueError, match="max_redirects"):
        outbound_http.SafeSession(max_redirects=11)
    with pytest.raises(ValueError, match="max_response_bytes"):
        outbound_http.SafeSession(max_response_bytes=32 * 1024 * 1024 + 1)
    with pytest.raises(ValueError, match="total_timeout_seconds"):
        outbound_http.SafeSession(total_timeout_seconds=301)


def test_bounded_response_rejects_chunked_body_without_content_length() -> None:
    response = requests.Response()
    response.status_code = 200
    response.url = "https://public.example/chunked"
    response.raw = _RawResponse(PUBLIC_IP)
    response._content_consumed = False
    response.iter_content = lambda **_kwargs: iter((b"x" * 10, b"y" * 10))

    with pytest.raises(outbound_http.OutboundResponseTooLarge):
        outbound_http._buffer_bounded_response(  # noqa: SLF001 - exercise the byte-limit boundary.
            response,
            maximum_bytes=16,
            deadline=outbound_http.time.monotonic() + 5,
        )
    assert response.raw.closed is True


def test_bounded_gzip_decompress_rejects_bombs_and_ambiguous_streams() -> None:
    assert outbound_http.bounded_gzip_decompress(gzip.compress(b"safe")) == b"safe"

    bomb = gzip.compress(b"x" * 1025)
    with pytest.raises(outbound_http.OutboundResponseTooLarge):
        outbound_http.bounded_gzip_decompress(bomb, maximum_bytes=1024)

    with pytest.raises(outbound_http.OutboundResponseInvalid, match="trailing"):
        outbound_http.bounded_gzip_decompress(gzip.compress(b"one") + gzip.compress(b"two"))
    with pytest.raises(outbound_http.OutboundResponseInvalid, match="truncated"):
        outbound_http.bounded_gzip_decompress(gzip.compress(b"cut")[:-2])


def test_pinned_adapter_uses_validated_ip_and_original_tls_hostname(monkeypatch) -> None:
    captured: dict[str, object] = {}
    adapter = outbound_http._PinnedHTTPAdapter()  # noqa: SLF001 - verify the security boundary itself.
    monkeypatch.setattr(
        adapter.poolmanager,
        "connection_from_host",
        lambda **kwargs: captured.update(kwargs) or object(),
    )
    request = requests.Request("GET", "https://public.example/path").prepare()
    request._mooncen_selected_address = PUBLIC_IP
    request._mooncen_original_hostname = "public.example"

    adapter.get_connection_with_tls_context(request, verify=True, proxies={})

    assert captured["host"] == PUBLIC_IP
    assert captured["scheme"] == "https"
    pool_kwargs = captured["pool_kwargs"]
    assert pool_kwargs["assert_hostname"] == "public.example"
    assert pool_kwargs["server_hostname"] == "public.example"
    assert "ssl_context" not in pool_kwargs


@pytest.mark.parametrize(
    "hostname",
    [
        "dylib.jne.go.kr",
        "grlib.jne.go.kr",
        "gslib.jne.go.kr",
        "gylib.jne.go.kr",
        "hnlib.jne.go.kr",
        "gylife.jne.go.kr",
        "yalib.jne.go.kr",
    ],
)
def test_jne_tls_compatibility_profile_remains_strict_and_host_scoped(monkeypatch, hostname) -> None:
    context = outbound_http._tls12_rsa_aes256_gcm_context()  # noqa: SLF001
    assert context.minimum_version == ssl.TLSVersion.TLSv1_2
    assert context.maximum_version == ssl.TLSVersion.TLSv1_2
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert "AES256-GCM-SHA384" in {cipher["name"] for cipher in context.get_ciphers()}

    captured: dict[str, object] = {}
    adapter = outbound_http._PinnedHTTPAdapter()  # noqa: SLF001
    monkeypatch.setattr(
        adapter.poolmanager,
        "connection_from_host",
        lambda **kwargs: captured.update(kwargs) or object(),
    )
    request = requests.Request(
        "GET",
        f"https://{hostname}/lecture.es?mid=a50402000000",
    ).prepare()
    request._mooncen_selected_address = PUBLIC_IP
    request._mooncen_original_hostname = hostname

    adapter.get_connection_with_tls_context(request, verify=True, proxies={})

    pool_kwargs = captured["pool_kwargs"]
    assert pool_kwargs["ssl_context"] is context
    assert pool_kwargs["assert_hostname"] == hostname
    assert pool_kwargs["server_hostname"] == hostname


def test_sciport_missing_intermediate_profile_remains_strict_and_host_scoped(monkeypatch) -> None:
    context = outbound_http._sciport_certificate_chain_context()  # noqa: SLF001
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert outbound_http._tls_compatibility_context("www.sciport.or.kr") is context  # noqa: SLF001
    assert outbound_http._tls_compatibility_context("lifelong.inje.go.kr") is context  # noqa: SLF001
    assert outbound_http._tls_compatibility_context("www.gjcf.or.kr") is context  # noqa: SLF001
    assert outbound_http._tls_compatibility_context("dmgj.kr") is context  # noqa: SLF001
    assert outbound_http._tls_compatibility_context("sciport.or.kr") is None  # noqa: SLF001
    assert outbound_http._tls_compatibility_context("evil.www.sciport.or.kr") is None  # noqa: SLF001
    assert outbound_http._tls_compatibility_context("inje.go.kr") is None  # noqa: SLF001
    assert outbound_http._tls_compatibility_context("evil.lifelong.inje.go.kr") is None  # noqa: SLF001
    assert outbound_http._tls_compatibility_context("gjcf.or.kr") is None  # noqa: SLF001
    assert outbound_http._tls_compatibility_context("evil.www.gjcf.or.kr") is None  # noqa: SLF001
    assert outbound_http._tls_compatibility_context("www.dmgj.kr") is None  # noqa: SLF001
    assert outbound_http._tls_compatibility_context("evil.dmgj.kr") is None  # noqa: SLF001

    captured: dict[str, object] = {}
    adapter = outbound_http._PinnedHTTPAdapter()  # noqa: SLF001
    monkeypatch.setattr(
        adapter.poolmanager,
        "connection_from_host",
        lambda **kwargs: captured.update(kwargs) or object(),
    )
    request = requests.Request(
        "GET",
        "https://www.sciport.or.kr/kor/CMS/IndivCurriMgr/curriList.do?mCode=MN038",
    ).prepare()
    request._mooncen_selected_address = PUBLIC_IP
    request._mooncen_original_hostname = "www.sciport.or.kr"

    adapter.get_connection_with_tls_context(request, verify=True, proxies={})

    pool_kwargs = captured["pool_kwargs"]
    assert pool_kwargs["ssl_context"] is context
    assert pool_kwargs["assert_hostname"] == "www.sciport.or.kr"
    assert pool_kwargs["server_hostname"] == "www.sciport.or.kr"


def test_gbmg_missing_intermediate_profile_remains_strict_and_host_scoped(monkeypatch) -> None:
    context = outbound_http._gbmg_certificate_chain_context()  # noqa: SLF001
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert outbound_http._tls_compatibility_context("www.gbmg.go.kr") is context  # noqa: SLF001
    assert outbound_http._tls_compatibility_context("use.go.kr") is context  # noqa: SLF001
    assert outbound_http._tls_compatibility_context("gbmg.go.kr") is None  # noqa: SLF001
    assert outbound_http._tls_compatibility_context("other.gbmg.go.kr") is None  # noqa: SLF001
    assert outbound_http._tls_compatibility_context("evil.www.gbmg.go.kr") is None  # noqa: SLF001
    assert outbound_http._tls_compatibility_context("www.gbmg.go.kr.evil.example") is None  # noqa: SLF001
    assert outbound_http._tls_compatibility_context("evil.use.go.kr") is None  # noqa: SLF001
    assert outbound_http._tls_compatibility_context("use.go.kr.evil.example") is None  # noqa: SLF001
    assert outbound_http._tls_compatibility_context("www.sciport.or.kr") is not context  # noqa: SLF001

    captured: dict[str, object] = {}
    adapter = outbound_http._PinnedHTTPAdapter()  # noqa: SLF001
    monkeypatch.setattr(
        adapter.poolmanager,
        "connection_from_host",
        lambda **kwargs: captured.update(kwargs) or object(),
    )
    request = requests.Request(
        "GET",
        "https://www.gbmg.go.kr/reservation/youthCulture/lecture/list.do?mId=0109020000",
    ).prepare()
    request._mooncen_selected_address = PUBLIC_IP
    request._mooncen_original_hostname = "www.gbmg.go.kr"

    adapter.get_connection_with_tls_context(request, verify=True, proxies={})

    pool_kwargs = captured["pool_kwargs"]
    assert pool_kwargs["ssl_context"] is context
    assert pool_kwargs["assert_hostname"] == "www.gbmg.go.kr"
    assert pool_kwargs["server_hostname"] == "www.gbmg.go.kr"


def test_uljin_missing_intermediate_profile_remains_strict_and_host_scoped() -> None:
    context = outbound_http._uljin_library_certificate_chain_context()  # noqa: SLF001

    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert outbound_http._tls_compatibility_context("lib.uljin.go.kr") is context  # noqa: SLF001
    assert outbound_http._tls_compatibility_context("uljin.go.kr") is None  # noqa: SLF001
    assert outbound_http._tls_compatibility_context("evil.lib.uljin.go.kr") is None  # noqa: SLF001
    assert outbound_http._tls_compatibility_context("lib.uljin.go.kr.evil.example") is None  # noqa: SLF001


@pytest.mark.parametrize(
    "hostname",
    [
        "library.daegu.go.kr",
        "library-ssl.daegu.go.kr",
        "library.sokcho.go.kr",
    ],
)
def test_daegu_library_tls_profile_remains_strict_and_host_scoped(monkeypatch, hostname) -> None:
    context = outbound_http._daegu_library_tls_compatibility_context()  # noqa: SLF001
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert outbound_http._tls_compatibility_context(hostname) is context  # noqa: SLF001
    assert outbound_http._tls_compatibility_context(f"evil.{hostname}") is None  # noqa: SLF001

    captured: dict[str, object] = {}
    adapter = outbound_http._PinnedHTTPAdapter()  # noqa: SLF001
    monkeypatch.setattr(
        adapter.poolmanager,
        "connection_from_host",
        lambda **kwargs: captured.update(kwargs) or object(),
    )
    request = requests.Request("GET", f"https://{hostname}/").prepare()
    request._mooncen_selected_address = PUBLIC_IP
    request._mooncen_original_hostname = hostname

    adapter.get_connection_with_tls_context(request, verify=True, proxies={})

    pool_kwargs = captured["pool_kwargs"]
    assert pool_kwargs["ssl_context"] is context
    assert pool_kwargs["assert_hostname"] == hostname
    assert pool_kwargs["server_hostname"] == hostname


def test_production_crawler_factories_use_safe_sessions() -> None:
    assert isinstance(Crawler_MunicipalYaml.session(), outbound_http.SafeSession)
    assert isinstance(library_usage_info.make_session(), outbound_http.SafeSession)
    sample_source = (ROOT / "tools" / "sample_collect_from_yaml.py").read_text(encoding="utf-8")
    assert "SafeSession(" in sample_source
    assert "requests.Session()" not in sample_source
    assert "outbound_request_budget" in sample_source


def test_all_non_browser_crawler_clients_use_the_safe_session_boundary() -> None:
    crawler_files = (
        "Crawler_Emart.py",
        "Crawler_EsongpaSportsCulture.py",
        "Crawler_Homeplus.py",
        "Crawler_Lotte.py",
        "Crawler_Sahasilver.py",
        "Crawler_SeongnamBaeumsoop.py",
        "Crawler_SeosanReservation.py",
    )
    for filename in crawler_files:
        source = (ROOT / "Crawler" / filename).read_text(encoding="utf-8")
        assert "SafeSession" in source, filename
        assert "requests.Session()" not in source, filename
        assert "requests.get(" not in source, filename
        assert "requests.post(" not in source, filename

    refresh_worker = (ROOT / "tools" / "maintenance" / "refresh_course_status.py").read_text(encoding="utf-8")
    assert "session = SafeSession(" in refresh_worker
    assert "session = requests.Session()" not in refresh_worker
    assert "harden_session(session)" in refresh_worker


def test_browser_crawlers_keep_chrome_sandbox_enabled() -> None:
    browser_clients = (
        ROOT / "Crawler" / "Crawler_Emart.py",
        ROOT / "Crawler" / "Crawler_Homeplus.py",
        ROOT / "Crawler" / "Crawler_Lotte.py",
        ROOT / "tools" / "maintenance" / "update_branch_gis.py",
    )
    for path in browser_clients:
        source = path.read_text(encoding="utf-8")
        filename = str(path.relative_to(ROOT))
        assert "--no-sandbox" not in source, filename
        assert "webdriver.Chrome(options=options)" not in source, filename
        assert "build_chrome_driver(options)" in source, filename

    for filename in ("mooncen-crawler.service", "mooncen-crawler-once.service"):
        source = (ROOT / "deploy" / "ubuntu" / "systemd" / filename).read_text(encoding="utf-8")
        assert "User=mooncen-crawler" in source
        assert "MemoryHigh=70%" in source
        assert "MemoryMax=80%" in source
        assert "TasksMax=512" in source
        assert "ExecStartPre=/opt/mooncen/.venv/bin/python -I /opt/mooncen/tools/chrome_sandbox_smoke.py" in source

    smoke = (ROOT / "tools" / "chrome_sandbox_smoke.py").read_text(encoding="utf-8")
    assert '"--no-sandbox"' not in smoke
    assert '"--headless=new"' in smoke
    assert "driver = build_chrome_driver(options)" in smoke
    assert "driver.find_element" in smoke
