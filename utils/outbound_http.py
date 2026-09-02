from __future__ import annotations

import contextvars
import ipaddress
import os
import socket
import ssl
import time
import zlib
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from functools import lru_cache
from typing import Any, Optional
from urllib.parse import urljoin, urlsplit

import requests
from requests.adapters import HTTPAdapter
from requests.cookies import extract_cookies_to_jar
from requests.exceptions import RequestException
from requests.hooks import dispatch_hook
from requests.sessions import preferred_clock


DEFAULT_CONNECT_READ_TIMEOUT_SECONDS = 20.0
DEFAULT_TOTAL_TIMEOUT_SECONDS = 90.0
DEFAULT_MAX_REDIRECTS = 5
DEFAULT_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_DECOMPRESSED_BYTES = 8 * 1024 * 1024
MAX_URL_LENGTH = 8192

# These official JNE library hosts share a legacy endpoint that only negotiates
# a TLS 1.2 static-RSA suite. Keep the exceptions exact and code-owned so target
# configuration cannot weaken TLS for arbitrary hosts. Certificate-chain and
# hostname verification remain enabled.
_TLS12_RSA_AES256_GCM_SHA384_HOSTS = frozenset(
    {
        "dylib.jne.go.kr",
        "grlib.jne.go.kr",
        "gslib.jne.go.kr",
        "gylib.jne.go.kr",
        "hnlib.jne.go.kr",
    }
)
_TLS12_RSA_AES256_GCM_SHA384_CIPHER = "AES256-GCM-SHA384"

# www.sciport.or.kr currently omits this Sectigo intermediate from its TLS
# handshake. Add the public intermediate only for that exact host while keeping
# root-chain and hostname verification enabled. SHA-256 (DER):
# 8c54c334b66ba4e426772af4a3f9136c19a1aec729fdb28c535c07a5a4ef22e0
_SCIPORT_HOST = "www.sciport.or.kr"
_INJE_LIFELONG_HOST = "lifelong.inje.go.kr"
_GWANGJU_CULTURAL_FOUNDATION_HOST = "www.gjcf.or.kr"
_DONGMYEONG_CULTURE_HOST = "dmgj.kr"
_SECTIGO_DV_R36_MISSING_INTERMEDIATE_HOSTS = frozenset(
    {
        _SCIPORT_HOST,
        _INJE_LIFELONG_HOST,
        _GWANGJU_CULTURAL_FOUNDATION_HOST,
        _DONGMYEONG_CULTURE_HOST,
    }
)
_GBMG_HOST = "www.gbmg.go.kr"
_ULSAN_EDU_BOOKING_HOST = "use.go.kr"
_SECTIGO_OV_MISSING_INTERMEDIATE_HOSTS = frozenset({_GBMG_HOST, _ULSAN_EDU_BOOKING_HOST})
# These exact public-library hosts require OpenSSL security level 1 while
# still presenting certificate chains accepted by the default trust store.
_DAEGU_LIBRARY_TLS_COMPATIBILITY_HOSTS = frozenset(
    {
        "library.daegu.go.kr",
        "library-ssl.daegu.go.kr",
        "library.sokcho.go.kr",
    }
)
_ULJIN_LIBRARY_HOST = "lib.uljin.go.kr"
_RAPIDSSL_TLS_RSA_CA_G1_PEM = """-----BEGIN CERTIFICATE-----
MIIEszCCA5ugAwIBAgIQCyWUIs7ZgSoVoE6ZUooO+jANBgkqhkiG9w0BAQsFADBh
MQswCQYDVQQGEwJVUzEVMBMGA1UEChMMRGlnaUNlcnQgSW5jMRkwFwYDVQQLExB3
d3cuZGlnaWNlcnQuY29tMSAwHgYDVQQDExdEaWdpQ2VydCBHbG9iYWwgUm9vdCBH
MjAeFw0xNzExMDIxMjI0MzNaFw0yNzExMDIxMjI0MzNaMGAxCzAJBgNVBAYTAlVT
MRUwEwYDVQQKEwxEaWdpQ2VydCBJbmMxGTAXBgNVBAsTEHd3dy5kaWdpY2VydC5j
b20xHzAdBgNVBAMTFlJhcGlkU1NMIFRMUyBSU0EgQ0EgRzEwggEiMA0GCSqGSIb3
DQEBAQUAA4IBDwAwggEKAoIBAQC/uVklRBI1FuJdUEkFCuDL/I3aJQiaZ6aibRHj
ap/ap9zy1aYNrphe7YcaNwMoPsZvXDR+hNJOo9gbgOYVTPq8gXc84I75YKOHiVA4
NrJJQZ6p2sJQyqx60HkEIjzIN+1LQLfXTlpuznToOa1hyTD0yyitFyOYwURM+/CI
8FNFMpBhw22hpeAQkOOLmsqT5QZJYeik7qlvn8gfD+XdDnk3kkuuu0eG+vuyrSGr
5uX5LRhFWlv1zFQDch/EKmd163m6z/ycx/qLa9zyvILc7cQpb+k7TLra9WE17YPS
n9ANjG+ECo9PDW3N9lwhKQCNvw1gGoguyCQu7HE7BnW8eSSFAgMBAAGjggFmMIIB
YjAdBgNVHQ4EFgQUDNtsgkkPSmcKuBTuesRIUojrVjgwHwYDVR0jBBgwFoAUTiJU
IBiV5uNu5g/6+rkS7QYXjzkwDgYDVR0PAQH/BAQDAgGGMB0GA1UdJQQWMBQGCCsG
AQUFBwMBBggrBgEFBQcDAjASBgNVHRMBAf8ECDAGAQH/AgEAMDQGCCsGAQUFBwEB
BCgwJjAkBggrBgEFBQcwAYYYaHR0cDovL29jc3AuZGlnaWNlcnQuY29tMEIGA1Ud
HwQ7MDkwN6A1oDOGMWh0dHA6Ly9jcmwzLmRpZ2ljZXJ0LmNvbS9EaWdpQ2VydEds
b2JhbFJvb3RHMi5jcmwwYwYDVR0gBFwwWjA3BglghkgBhv1sAQEwKjAoBggrBgEF
BQcCARYcaHR0cHM6Ly93d3cuZGlnaWNlcnQuY29tL0NQUzALBglghkgBhv1sAQIw
CAYGZ4EMAQIBMAgGBmeBDAECAjANBgkqhkiG9w0BAQsFAAOCAQEAGUSlOb4K3Wtm
SlbmE50UYBHXM0SKXPqHMzk6XQUpCheF/4qU8aOhajsyRQFDV1ih/uPIg7YHRtFi
CTq4G+zb43X1T77nJgSOI9pq/TqCwtukZ7u9VLL3JAq3Wdy2moKLvvC8tVmRzkAe
0xQCkRKIjbBG80MSyDX/R4uYgj6ZiNT/Zg6GI6RofgqgpDdssLc0XIRQEotxIZcK
zP3pGJ9FCbMHmMLLyuBd+uCWvVcF2ogYAawufChS/PT61D9rqzPRS5I2uqa3tmIT
44JhJgWhBnFMb7AGQkvNq9KNS9dd3GWc17H/dXa1enoxzWjE0hBdFjxPhUb0W3wi
8o34/m8Fxw==
-----END CERTIFICATE-----
"""
_SECTIGO_PUBLIC_SERVER_AUTH_CA_DV_R36_PEM = """-----BEGIN CERTIFICATE-----
MIIGTDCCBDSgAwIBAgIQOXpmzCdWNi4NqofKbqvjsTANBgkqhkiG9w0BAQwFADBf
MQswCQYDVQQGEwJHQjEYMBYGA1UEChMPU2VjdGlnbyBMaW1pdGVkMTYwNAYDVQQD
Ey1TZWN0aWdvIFB1YmxpYyBTZXJ2ZXIgQXV0aGVudGljYXRpb24gUm9vdCBSNDYw
HhcNMjEwMzIyMDAwMDAwWhcNMzYwMzIxMjM1OTU5WjBgMQswCQYDVQQGEwJHQjEY
MBYGA1UEChMPU2VjdGlnbyBMaW1pdGVkMTcwNQYDVQQDEy5TZWN0aWdvIFB1Ymxp
YyBTZXJ2ZXIgQXV0aGVudGljYXRpb24gQ0EgRFYgUjM2MIIBojANBgkqhkiG9w0B
AQEFAAOCAY8AMIIBigKCAYEAljZf2HIz7+SPUPQCQObZYcrxLTHYdf1ZtMRe7Yeq
RPSwygz16qJ9cAWtWNTcuICc++p8Dct7zNGxCpqmEtqifO7NvuB5dEVexXn9RFFH
12Hm+NtPRQgXIFjx6MSJcNWuVO3XGE57L1mHlcQYj+g4hny90aFh2SCZCDEVkAja
EMMfYPKuCjHuuF+bzHFb/9gV8P9+ekcHENF2nR1efGWSKwnfG5RawlkaQDpRtZTm
M64TIsv/r7cyFO4nSjs1jLdXYdz5q3a4L0NoabZfbdxVb+CUEHfB0bpulZQtH1Rv
38e/lIdP7OTTIlZh6OYL6NhxP8So0/sht/4J9mqIGxRFc0/pC8suja+wcIUna0HB
pXKfXTKpzgis+zmXDL06ASJf5E4A2/m+Hp6b84sfPAwQ766rI65mh50S0Di9E3Pn
2WcaJc+PILsBmYpgtmgWTR9eV9otfKRUBfzHUHcVgarub/XluEpRlTtZudU5xbFN
xx/DgMrXLUAPaI60fZ6wA+PTAgMBAAGjggGBMIIBfTAfBgNVHSMEGDAWgBRWc1hk
lfmSGrASKgRieaFAFYghSTAdBgNVHQ4EFgQUaMASFhgOr872h6YyV6NGUV3LBycw
DgYDVR0PAQH/BAQDAgGGMBIGA1UdEwEB/wQIMAYBAf8CAQAwHQYDVR0lBBYwFAYI
KwYBBQUHAwEGCCsGAQUFBwMCMBsGA1UdIAQUMBIwBgYEVR0gADAIBgZngQwBAgEw
VAYDVR0fBE0wSzBJoEegRYZDaHR0cDovL2NybC5zZWN0aWdvLmNvbS9TZWN0aWdv
UHVibGljU2VydmVyQXV0aGVudGljYXRpb25Sb290UjQ2LmNybDCBhAYIKwYBBQUH
AQEEeDB2ME8GCCsGAQUFBzAChkNodHRwOi8vY3J0LnNlY3RpZ28uY29tL1NlY3Rp
Z29QdWJsaWNTZXJ2ZXJBdXRoZW50aWNhdGlvblJvb3RSNDYucDdjMCMGCCsGAQUF
BzABhhdodHRwOi8vb2NzcC5zZWN0aWdvLmNvbTANBgkqhkiG9w0BAQwFAAOCAgEA
YtOC9Fy+TqECFw40IospI92kLGgoSZGPOSQXMBqmsGWZUQ7rux7cj1du6d9rD6C8
ze1B2eQjkrGkIL/OF1s7vSmgYVafsRoZd/IHUrkoQvX8FZwUsmPu7amgBfaY3g+d
q1x0jNGKb6I6Bzdl6LgMD9qxp+3i7GQOnd9J8LFSietY6Z4jUBzVoOoz8iAU84OF
h2HhAuiPw1ai0VnY38RTI+8kepGWVfGxfBWzwH9uIjeooIeaosVFvE8cmYUB4TSH
5dUyD0jHct2+8ceKEtIoFU/FfHq/mDaVnvcDCZXtIgitdMFQdMZaVehmObyhRdDD
4NQCs0gaI9AAgFj4L9QtkARzhQLNyRf87Kln+YU0lgCGr9HLg3rGO8q+Y4ppLsOd
unQZ6ZxPNGIfOApbPVf5hCe58EZwiWdHIMn9lPP6+F404y8NNugbQixBber+x536
WrZhFZLjEkhp7fFXf9r32rNPfb74X/U90Bdy4lzp3+X1ukh1BuMxA/EEhDoTOS3l
7ABvc7BYSQubQ2490OcdkIzUh3ZwDrakMVrbaTxUM2p24N6dB+ns2zptWCva6jzW
r8IWKIMxzxLPv5Kt3ePKcUdvkBU/smqujSczTzzSjIoR5QqQA6lN1ZRSnuHIWCvh
JEltkYnTAH41QJ6SAWO66GrrUESwN/cgZzL4JLEqz1Y=
-----END CERTIFICATE-----"""

# www.gbmg.go.kr and use.go.kr currently send only their leaf certificates.
# Trust the shared missing public Sectigo intermediate only for the exact host
# allowlist above while retaining the default root store, certificate
# verification, and hostname checks. SHA-256 (DER):
# 72a34ac2b424aed3f6b0b04755b88cc027dccc806fddb22b4cd7c47773973ec0
_SECTIGO_RSA_ORGANIZATION_VALIDATION_SECURE_SERVER_CA_PEM = """-----BEGIN CERTIFICATE-----
MIIGGTCCBAGgAwIBAgIQE31TnKp8MamkM3AZaIR6jTANBgkqhkiG9w0BAQwFADCB
iDELMAkGA1UEBhMCVVMxEzARBgNVBAgTCk5ldyBKZXJzZXkxFDASBgNVBAcTC0pl
cnNleSBDaXR5MR4wHAYDVQQKExVUaGUgVVNFUlRSVVNUIE5ldHdvcmsxLjAsBgNV
BAMTJVVTRVJUcnVzdCBSU0EgQ2VydGlmaWNhdGlvbiBBdXRob3JpdHkwHhcNMTgx
MTAyMDAwMDAwWhcNMzAxMjMxMjM1OTU5WjCBlTELMAkGA1UEBhMCR0IxGzAZBgNV
BAgTEkdyZWF0ZXIgTWFuY2hlc3RlcjEQMA4GA1UEBxMHU2FsZm9yZDEYMBYGA1UE
ChMPU2VjdGlnbyBMaW1pdGVkMT0wOwYDVQQDEzRTZWN0aWdvIFJTQSBPcmdhbml6
YXRpb24gVmFsaWRhdGlvbiBTZWN1cmUgU2VydmVyIENBMIIBIjANBgkqhkiG9w0B
AQEFAAOCAQ8AMIIBCgKCAQEAnJMCRkVKUkiS/FeN+S3qU76zLNXYqKXsW2kDwB0Q
9lkz3v4HSKjojHpnSvH1jcM3ZtAykffEnQRgxLVK4oOLp64m1F06XvjRFnG7ir1x
on3IzqJgJLBSoDpFUd54k2xiYPHkVpy3O/c8Vdjf1XoxfDV/ElFw4Sy+BKzL+k/h
fGVqwECn2XylY4QZ4ffK76q06Fha2ZnjJt+OErK43DOyNtoUHZZYQkBuCyKFHFEi
rsTIBkVtkuZntxkj5Ng2a4XQf8dS48+wdQHgibSov4o2TqPgbOuEQc6lL0giE5dQ
YkUeCaXMn2xXcEAG2yDoG9bzk4unMp63RBUJ16/9fAEc2wIDAQABo4IBbjCCAWow
HwYDVR0jBBgwFoAUU3m/WqorSs9UgOHYm8Cd8rIDZsswHQYDVR0OBBYEFBfZ1iUn
Z/kxwklD2TA2RIxsqU/rMA4GA1UdDwEB/wQEAwIBhjASBgNVHRMBAf8ECDAGAQH/
AgEAMB0GA1UdJQQWMBQGCCsGAQUFBwMBBggrBgEFBQcDAjAbBgNVHSAEFDASMAYG
BFUdIAAwCAYGZ4EMAQICMFAGA1UdHwRJMEcwRaBDoEGGP2h0dHA6Ly9jcmwudXNl
cnRydXN0LmNvbS9VU0VSVHJ1c3RSU0FDZXJ0aWZpY2F0aW9uQXV0aG9yaXR5LmNy
bDB2BggrBgEFBQcBAQRqMGgwPwYIKwYBBQUHMAKGM2h0dHA6Ly9jcnQudXNlcnRy
dXN0LmNvbS9VU0VSVHJ1c3RSU0FBZGRUcnVzdENBLmNydDAlBggrBgEFBQcwAYYZ
aHR0cDovL29jc3AudXNlcnRydXN0LmNvbTANBgkqhkiG9w0BAQwFAAOCAgEAThNA
lsnD5m5bwOO69Bfhrgkfyb/LDCUW8nNTs3Yat6tIBtbNAHwgRUNFbBZaGxNh10m6
pAKkrOjOzi3JKnSj3N6uq9BoNviRrzwB93fVC8+Xq+uH5xWo+jBaYXEgscBDxLmP
bYox6xU2JPti1Qucj+lmveZhUZeTth2HvbC1bP6mESkGYTQxMD0gJ3NR0N6Fg9N3
OSBGltqnxloWJ4Wyz04PToxcvr44APhL+XJ71PJ616IphdAEutNCLFGIUi7RPSRn
R+xVzBv0yjTqJsHe3cQhifa6ezIejpZehEU4z4CqN2mLYBd0FUiRnG3wTqN3yhsc
SPr5z0noX0+FCuKPkBurcEya67emP7SsXaRfz+bYipaQ908mgWB2XQ8kd5GzKjGf
FlqyXYwcKapInI5v03hAcNt37N3j0VcFcC3mSZiIBYRiBXBWdoY5TtMibx3+bfEO
s2LEPMvAhblhHrrhFYBZlAyuBbuMf1a+HNJav5fyakywxnB2sJCNwQs2uRHY1ihc
6k/+JLcYCpsM0MF8XPtpvcyiTcaQvKZN8rG61ppnW5YCUtCC+cQKXA0o4D/I+pWV
idWkvklsQLI+qGu41SWyxP7x09fn1txDAXYw+zuLXfdKiXyaNb78yvBXAfCNP6CH
MntHWpdLgtJmwsQt6j8k9Kf5qLnjatkYYaA7jBU=
-----END CERTIFICATE-----"""

_request_deadline: contextvars.ContextVar[float | None] = contextvars.ContextVar(
    "mooncen_outbound_request_deadline",
    default=None,
)
_request_budget: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "mooncen_outbound_request_budget",
    default=None,
)
# Detail collectors can issue more than four concurrent requests. Keep DNS
# validation off the request threads without letting executor queue time look
# like an upstream resolver timeout.
_dns_executor = ThreadPoolExecutor(max_workers=16, thread_name_prefix="mooncen-safe-dns")


class OutboundRequestBlocked(RequestException):
    """Raised before a crawler can connect to an unsafe destination."""


class OutboundResponseTooLarge(RequestException):
    """Raised when an upstream response exceeds the configured byte limit."""


class OutboundResponseInvalid(RequestException):
    """Raised when an encoded upstream response is malformed or ambiguous."""


@contextmanager
def outbound_request_budget(maximum_requests: int):
    """Bound all SafeSession hops in the current crawler execution context."""
    if isinstance(maximum_requests, bool) or not isinstance(maximum_requests, int):
        raise ValueError("maximum_requests must be an integer")
    if not 1 <= maximum_requests <= 10_000:
        raise ValueError("maximum_requests must be between 1 and 10000")
    existing = _request_budget.get()
    effective = min(existing, maximum_requests) if existing is not None else maximum_requests
    token = _request_budget.set(effective)
    try:
        yield
    finally:
        _request_budget.reset(token)


def _consume_request_budget() -> None:
    remaining = _request_budget.get()
    if remaining is None:
        return
    if remaining <= 0:
        raise OutboundRequestBlocked("Outbound request budget exhausted")
    _request_budget.set(remaining - 1)


def bounded_gzip_decompress(
    payload: bytes,
    *,
    maximum_bytes: int = DEFAULT_MAX_DECOMPRESSED_BYTES,
) -> bytes:
    """Decode one complete gzip member without permitting decompression bombs."""
    if not 1 <= maximum_bytes <= 32 * 1024 * 1024:
        raise ValueError("maximum_bytes must be between 1 and 33554432")
    if not payload.startswith(b"\x1f\x8b"):
        raise OutboundResponseInvalid("Outbound response is not gzip encoded")

    decoder = zlib.decompressobj(zlib.MAX_WBITS | 16)
    try:
        decoded = decoder.decompress(payload, maximum_bytes + 1)
        if len(decoded) > maximum_bytes or decoder.unconsumed_tail:
            raise OutboundResponseTooLarge(f"Decompressed response exceeds {maximum_bytes} bytes")
        remaining = maximum_bytes - len(decoded)
        decoded += decoder.flush(remaining + 1)
    except zlib.error as exc:
        raise OutboundResponseInvalid("Outbound gzip response is malformed") from exc

    if len(decoded) > maximum_bytes:
        raise OutboundResponseTooLarge(f"Decompressed response exceeds {maximum_bytes} bytes")
    if not decoder.eof:
        raise OutboundResponseInvalid("Outbound gzip response is truncated")
    if decoder.unused_data:
        raise OutboundResponseInvalid("Outbound gzip response contains trailing or concatenated data")
    return decoded


@dataclass(frozen=True)
class ValidatedDestination:
    url: str
    hostname: str
    port: int
    addresses: tuple[str, ...]

    @property
    def selected_address(self) -> str:
        return self.addresses[0]


def _bounded_env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise RuntimeError(f"{name} must be between {minimum} and {maximum}")
    return value


def _normalized_ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    address = ipaddress.ip_address(value.split("%", 1)[0])
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def _is_forbidden_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return bool(
        address.is_loopback
        or address.is_private
        or address.is_link_local
        or address.is_reserved
        or address.is_unspecified
        or address.is_multicast
        or not address.is_global
    )


def _ordered_addresses(values: Iterable[str]) -> tuple[str, ...]:
    unique: dict[str, None] = {}
    for value in values:
        normalized = str(_normalized_ip(value))
        unique.setdefault(normalized, None)
    # Prefer IPv4 where both families are available. This avoids selecting an
    # unreachable IPv6 route while still supporting IPv6-only institutions.
    return tuple(sorted(unique, key=lambda item: (":" in item, item)))


def resolve_public_addresses(hostname: str, port: int) -> tuple[str, ...]:
    normalized_host = hostname.strip().rstrip(".").lower()
    if not normalized_host:
        raise OutboundRequestBlocked("Outbound URL hostname is missing")

    try:
        literal = _normalized_ip(normalized_host)
    except ValueError:
        literal = None

    if literal is not None:
        addresses = (str(literal),)
    else:
        configured_dns_timeout = float(
            _bounded_env_int(
                "OUTBOUND_HTTP_DNS_TIMEOUT_SECONDS",
                5,
                1,
                15,
            )
        )
        deadline = _request_deadline.get()
        records = None
        timeout_error: Optional[FutureTimeoutError] = None
        for attempt in range(2):
            dns_timeout = configured_dns_timeout
            if deadline is not None:
                dns_timeout = min(
                    dns_timeout,
                    max(0.001, deadline - time.monotonic()),
                )
            future = _dns_executor.submit(
                socket.getaddrinfo,
                normalized_host,
                port,
                family=socket.AF_UNSPEC,
                type=socket.SOCK_STREAM,
            )
            try:
                records = future.result(timeout=dns_timeout)
                break
            except FutureTimeoutError as exc:
                future.cancel()
                timeout_error = exc
                if attempt == 0 and (deadline is None or deadline - time.monotonic() > 0.001):
                    continue
                raise OutboundRequestBlocked(f"Outbound hostname resolution timed out: {normalized_host}") from exc
            except (OSError, UnicodeError) as exc:
                raise OutboundRequestBlocked(f"Outbound hostname could not be resolved: {normalized_host}") from exc
        if records is None:
            raise OutboundRequestBlocked(
                f"Outbound hostname resolution timed out: {normalized_host}"
            ) from timeout_error
        try:
            addresses = _ordered_addresses(record[4][0] for record in records)
        except (TypeError, ValueError) as exc:
            raise OutboundRequestBlocked(f"Outbound hostname returned an invalid address: {normalized_host}") from exc

    if not addresses:
        raise OutboundRequestBlocked(f"Outbound hostname has no usable address: {normalized_host}")
    # There are deliberately no private-network exceptions: no current public
    # institution target requires one. Any future exception must be a narrow,
    # documented hostname rule rather than an environment-controlled bypass.
    for value in addresses:
        if _is_forbidden_address(_normalized_ip(value)):
            raise OutboundRequestBlocked(f"Outbound hostname resolves to a non-public address: {normalized_host}")
    return addresses


def validate_outbound_url(value: Any) -> ValidatedDestination:
    url = str(value or "").strip()
    if not url or len(url) > MAX_URL_LENGTH:
        raise OutboundRequestBlocked("Outbound URL is missing or too long")
    if any(ord(character) <= 32 or ord(character) == 127 for character in url):
        raise OutboundRequestBlocked("Outbound URL contains control characters")

    try:
        parsed = urlsplit(url)
        port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    except (TypeError, ValueError) as exc:
        raise OutboundRequestBlocked("Outbound URL is malformed") from exc

    if parsed.scheme.lower() not in {"http", "https"}:
        raise OutboundRequestBlocked("Outbound URL must use HTTP or HTTPS")
    if not parsed.hostname or parsed.username is not None or parsed.password is not None:
        raise OutboundRequestBlocked("Outbound URL hostname is missing or contains credentials")
    if not 1 <= port <= 65535:
        raise OutboundRequestBlocked("Outbound URL port is invalid")

    hostname = parsed.hostname.rstrip(".").lower()
    addresses = resolve_public_addresses(hostname, port)
    return ValidatedDestination(url=url, hostname=hostname, port=port, addresses=addresses)


def _host_header(request_url: str) -> str:
    parsed = urlsplit(request_url)
    hostname = parsed.hostname or ""
    host = f"[{hostname}]" if ":" in hostname and not hostname.startswith("[") else hostname
    port = parsed.port
    default_port = 443 if parsed.scheme.lower() == "https" else 80
    return f"{host}:{port}" if port and port != default_port else host


@lru_cache(maxsize=1)
def _tls12_rsa_aes256_gcm_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.maximum_version = ssl.TLSVersion.TLSv1_2
    context.set_ciphers(_TLS12_RSA_AES256_GCM_SHA384_CIPHER)
    return context


@lru_cache(maxsize=1)
def _sciport_certificate_chain_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.load_verify_locations(cadata=_SECTIGO_PUBLIC_SERVER_AUTH_CA_DV_R36_PEM)
    return context


@lru_cache(maxsize=1)
def _gbmg_certificate_chain_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.load_verify_locations(cadata=_SECTIGO_RSA_ORGANIZATION_VALIDATION_SECURE_SERVER_CA_PEM)
    return context


@lru_cache(maxsize=1)
def _daegu_library_tls_compatibility_context() -> ssl.SSLContext:
    """Retain certificate checks while allowing the official legacy endpoint."""

    context = ssl.create_default_context()
    context.set_ciphers("DEFAULT:@SECLEVEL=1")
    return context


@lru_cache(maxsize=1)
def _uljin_library_certificate_chain_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.load_verify_locations(cadata=_RAPIDSSL_TLS_RSA_CA_G1_PEM)
    return context


def _tls_compatibility_context(hostname: str) -> ssl.SSLContext | None:
    normalized_host = hostname.strip().rstrip(".").lower()
    if normalized_host in _TLS12_RSA_AES256_GCM_SHA384_HOSTS or normalized_host.endswith(".jne.go.kr"):
        return _tls12_rsa_aes256_gcm_context()
    if normalized_host in _SECTIGO_DV_R36_MISSING_INTERMEDIATE_HOSTS:
        return _sciport_certificate_chain_context()
    if normalized_host in _SECTIGO_OV_MISSING_INTERMEDIATE_HOSTS:
        return _gbmg_certificate_chain_context()
    if normalized_host in _DAEGU_LIBRARY_TLS_COMPATIBILITY_HOSTS:
        return _daegu_library_tls_compatibility_context()
    if normalized_host == _ULJIN_LIBRARY_HOST:
        return _uljin_library_certificate_chain_context()
    return None


class _PinnedHTTPAdapter(HTTPAdapter):
    """Connect to the address validated by SafeSession while retaining SNI."""

    def get_connection_with_tls_context(
        self,
        request: requests.PreparedRequest,
        verify: Any,
        proxies: dict[str, str] | None = None,
        cert: Any = None,
    ):
        if proxies and any(value for value in proxies.values()):
            raise OutboundRequestBlocked("Outbound HTTP proxies are not permitted")

        selected_address = getattr(request, "_mooncen_selected_address", "")
        original_hostname = getattr(request, "_mooncen_original_hostname", "")
        if not selected_address or not original_hostname:
            raise OutboundRequestBlocked("Outbound request destination was not validated")

        host_params, pool_kwargs = self.build_connection_pool_key_attributes(
            request,
            verify,
            cert,
        )
        host_params["host"] = selected_address
        if host_params["scheme"] == "https":
            pool_kwargs["assert_hostname"] = original_hostname
            pool_kwargs["server_hostname"] = original_hostname
            compatibility_context = _tls_compatibility_context(original_hostname)
            if compatibility_context is not None:
                pool_kwargs["ssl_context"] = compatibility_context
        return self.poolmanager.connection_from_host(
            **host_params,
            pool_kwargs=pool_kwargs,
        )


def _peer_address(response: requests.Response) -> str | None:
    raw = response.raw
    connection = getattr(raw, "connection", None) or getattr(raw, "_connection", None)
    sockets = [getattr(connection, "sock", None)]

    # urllib3 has changed its wrapper layout across releases. The second path
    # covers responses whose connection wrapper has already detached the sock.
    nested = getattr(getattr(getattr(raw, "_fp", None), "fp", None), "raw", None)
    sockets.append(getattr(nested, "_sock", None))
    for candidate in sockets:
        if candidate is None:
            continue
        try:
            return str(_normalized_ip(candidate.getpeername()[0]))
        except (OSError, ValueError, TypeError):
            continue
    return None


def _bounded_timeout(value: Any, remaining: float) -> float | tuple[float, float]:
    maximum = float(
        _bounded_env_int(
            "OUTBOUND_HTTP_MAX_TIMEOUT_SECONDS",
            int(DEFAULT_CONNECT_READ_TIMEOUT_SECONDS),
            1,
            120,
        )
    )
    cap = max(0.001, min(maximum, remaining))

    def component(item: Any) -> float:
        if item is None:
            return cap
        try:
            parsed = float(item)
        except (TypeError, ValueError) as exc:
            raise OutboundRequestBlocked("Outbound request timeout is invalid") from exc
        if parsed <= 0:
            raise OutboundRequestBlocked("Outbound request timeout must be positive")
        return min(parsed, cap)

    if isinstance(value, tuple):
        if len(value) != 2:
            raise OutboundRequestBlocked("Outbound request timeout tuple is invalid")
        return component(value[0]), component(value[1])
    return component(value)


def _buffer_bounded_response(
    response: requests.Response,
    *,
    maximum_bytes: int,
    deadline: float,
) -> None:
    content_length = response.headers.get("Content-Length", "").strip()
    if content_length.isdigit() and int(content_length) > maximum_bytes:
        response.close()
        raise OutboundResponseTooLarge(
            f"Outbound response exceeds {maximum_bytes} bytes",
            response=response,
        )

    if response._content_consumed:  # noqa: SLF001 - requests exposes no bounded-buffer hook.
        content = response.content
        if len(content) > maximum_bytes:
            response.close()
            raise OutboundResponseTooLarge(
                f"Outbound response exceeds {maximum_bytes} bytes",
                response=response,
            )
        return

    chunks: list[bytes] = []
    size = 0
    try:
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if time.monotonic() > deadline:
                raise requests.Timeout("Outbound request exceeded its total timeout")
            if not chunk:
                continue
            size += len(chunk)
            if size > maximum_bytes:
                raise OutboundResponseTooLarge(
                    f"Outbound response exceeds {maximum_bytes} bytes",
                    response=response,
                )
            chunks.append(chunk)
        response._content = b"".join(chunks)  # noqa: SLF001
        response._content_consumed = True  # noqa: SLF001
    except Exception:
        response.close()
        raise
    finally:
        if response._content_consumed:  # noqa: SLF001
            response.close()


class SafeSession(requests.Session):
    """A requests session with SSRF, redirect, timeout, and body-size guards."""

    def __init__(
        self,
        *,
        max_redirects: int | None = None,
        max_response_bytes: int | None = None,
        total_timeout_seconds: int | None = None,
    ) -> None:
        super().__init__()
        self.trust_env = False
        configured_redirects = (
            max_redirects
            if max_redirects is not None
            else _bounded_env_int(
                "OUTBOUND_HTTP_MAX_REDIRECTS",
                DEFAULT_MAX_REDIRECTS,
                1,
                10,
            )
        )
        configured_response_bytes = (
            max_response_bytes
            if max_response_bytes is not None
            else _bounded_env_int(
                "OUTBOUND_HTTP_MAX_RESPONSE_BYTES",
                DEFAULT_MAX_RESPONSE_BYTES,
                64 * 1024,
                32 * 1024 * 1024,
            )
        )
        configured_total_timeout = (
            total_timeout_seconds
            if total_timeout_seconds is not None
            else _bounded_env_int(
                "OUTBOUND_HTTP_TOTAL_TIMEOUT_SECONDS",
                int(DEFAULT_TOTAL_TIMEOUT_SECONDS),
                1,
                300,
            )
        )
        if not 1 <= configured_redirects <= 10:
            raise ValueError("max_redirects must be between 1 and 10")
        if not 1 <= configured_response_bytes <= 32 * 1024 * 1024:
            raise ValueError("max_response_bytes must be between 1 and 33554432")
        if not 1 <= configured_total_timeout <= 300:
            raise ValueError("total_timeout_seconds must be between 1 and 300")
        self.max_redirects = configured_redirects
        self.max_response_bytes = configured_response_bytes
        self.total_timeout_seconds = configured_total_timeout
        adapter = _PinnedHTTPAdapter(max_retries=0)
        self.mount("http://", adapter)
        self.mount("https://", adapter)

    def request(self, method: str | bytes, url: str | bytes, **kwargs: Any) -> requests.Response:
        existing_deadline = _request_deadline.get()
        token: contextvars.Token[float | None] | None = None
        if existing_deadline is None:
            token = _request_deadline.set(time.monotonic() + self.total_timeout_seconds)
        try:
            return super().request(method, url, **kwargs)
        finally:
            if token is not None:
                _request_deadline.reset(token)

    def get_redirect_target(self, response: requests.Response) -> str | None:
        target = super().get_redirect_target(response)
        if not target:
            return target
        request_url = str(getattr(getattr(response, "request", None), "url", "") or response.url)
        redirect_url = urljoin(request_url, target)
        if urlsplit(request_url).scheme.lower() == "https" and urlsplit(redirect_url).scheme.lower() == "http":
            response.close()
            raise OutboundRequestBlocked(
                "HTTPS request attempted a plaintext redirect",
                response=response,
            )
        return target

    def send(self, request: requests.PreparedRequest, **kwargs: Any) -> requests.Response:
        _consume_request_budget()
        deadline = _request_deadline.get() or (time.monotonic() + self.total_timeout_seconds)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise requests.Timeout("Outbound request exceeded its total timeout")

        destination = validate_outbound_url(request.url)
        effective_verify = kwargs.get("verify", self.verify)
        if request.url.lower().startswith("https://") and effective_verify is False:
            raise OutboundRequestBlocked("TLS verification cannot be disabled")
        proxies = kwargs.get("proxies") or {}
        if any(value for value in proxies.values()):
            raise OutboundRequestBlocked("Outbound HTTP proxies are not permitted")

        request.headers["Host"] = _host_header(request.url)
        request._mooncen_selected_address = destination.selected_address  # type: ignore[attr-defined]
        request._mooncen_original_hostname = destination.hostname  # type: ignore[attr-defined]
        request._mooncen_validated_addresses = destination.addresses  # type: ignore[attr-defined]

        allow_redirects = bool(kwargs.pop("allow_redirects", False))
        kwargs["timeout"] = _bounded_timeout(kwargs.get("timeout"), remaining)
        kwargs["stream"] = True
        kwargs.setdefault("verify", self.verify)
        kwargs.setdefault("cert", self.cert)
        kwargs.setdefault("proxies", {})

        # requests.Session.send consumes a redirect response body even when
        # allow_redirects=False in order to prepare Response.next. Send exactly
        # one hop through the adapter so peer and body limits run first.
        adapter = self.get_adapter(url=request.url)
        started = preferred_clock()
        response = adapter.send(request, **kwargs)
        response.elapsed = timedelta(seconds=preferred_clock() - started)
        response = dispatch_hook("response", request.hooks, response, **kwargs)

        peer = _peer_address(response)
        if peer is not None and peer != destination.selected_address:
            response.close()
            raise OutboundRequestBlocked(
                "Outbound connection peer did not match the validated address",
                response=response,
            )
        if peer is not None and _is_forbidden_address(_normalized_ip(peer)):
            response.close()
            raise OutboundRequestBlocked(
                "Outbound connection reached a non-public address",
                response=response,
            )

        # Re-resolve after the connection. A changed or newly private answer is
        # treated as DNS rebinding even though the adapter pinned this hop.
        final_destination = validate_outbound_url(response.url)
        if destination.hostname != final_destination.hostname:
            response.close()
            raise OutboundRequestBlocked(
                "Outbound response URL changed before redirect validation",
                response=response,
            )

        _buffer_bounded_response(
            response,
            maximum_bytes=self.max_response_bytes,
            deadline=deadline,
        )
        if response.history:
            for historical_response in response.history:
                extract_cookies_to_jar(
                    self.cookies,
                    historical_response.request,
                    historical_response.raw,
                )
        extract_cookies_to_jar(self.cookies, request, response.raw)

        if allow_redirects:
            history: list[requests.Response] = []
            for redirected in self.resolve_redirects(response, request, **kwargs):
                history.append(response)
                response = redirected
            response.history = history
        return response


def harden_session(session: requests.Session | None = None) -> SafeSession:
    if isinstance(session, SafeSession):
        return session
    hardened = SafeSession()
    if session is None:
        return hardened

    hardened.headers.update(session.headers)
    hardened.cookies.update(session.cookies)
    hardened.auth = session.auth
    hardened.cert = session.cert
    hardened.verify = session.verify
    hardened.hooks = {name: hooks[:] for name, hooks in session.hooks.items()}
    hardened.params.update(session.params)
    return hardened
