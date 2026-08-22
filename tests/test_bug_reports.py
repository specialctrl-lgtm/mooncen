from __future__ import annotations

import base64
from email.message import EmailMessage
from types import SimpleNamespace
from uuid import uuid4
import zlib

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.routers import auth
from backend.routers.auth import get_current_user
from backend.services import bug_report_email


_MAIL_ENV_NAMES = (
    "MOONCEN_BUG_REPORT_TO",
    "MOONCEN_BUG_REPORT_FROM",
    "MOONCEN_SMTP_HOST",
    "MOONCEN_SMTP_PORT",
    "MOONCEN_SMTP_USERNAME",
    "MOONCEN_SMTP_PASSWORD",
    "MOONCEN_SMTP_SECURITY",
)


@pytest.fixture(autouse=True)
def reset_bug_report_state(monkeypatch):
    app.dependency_overrides.clear()
    with auth._rate_limit_lock:
        auth._rate_limit_buckets.clear()
    for name in _MAIL_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    yield
    app.dependency_overrides.clear()


def _chunk(chunk_type: bytes, payload: bytes) -> bytes:
    checksum = zlib.crc32(chunk_type + payload) & 0xFFFFFFFF
    return len(payload).to_bytes(4, "big") + chunk_type + payload + checksum.to_bytes(4, "big")


def _png(width: int = 2, height: int = 3) -> bytes:
    ihdr = (
        width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + bytes((8, 6, 0, 0, 0))
    )
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", zlib.compress(b"\0")) + _chunk(b"IEND", b"")


def _jpeg(width: int = 2, height: int = 3) -> bytes:
    frame = bytes((8,)) + height.to_bytes(2, "big") + width.to_bytes(2, "big") + b"\x01\x01\x11\x00"
    scan = b"\x01\x01\x00\x00\x3f\x00"
    return (
        b"\xff\xd8"
        + b"\xff\xc0"
        + (len(frame) + 2).to_bytes(2, "big")
        + frame
        + b"\xff\xda"
        + (len(scan) + 2).to_bytes(2, "big")
        + scan
        + b"\x01\x02\x03"
        + b"\xff\xd9"
    )


def _webp(width: int = 2, height: int = 3) -> bytes:
    packed = (width - 1) | ((height - 1) << 14)
    payload = b"\x2f" + packed.to_bytes(4, "little") + b"\0"
    chunk = b"VP8L" + len(payload).to_bytes(4, "little") + payload
    return b"RIFF" + (len(chunk) + 4).to_bytes(4, "little") + b"WEBP" + chunk


def _image_payload(content: bytes, media_type: str, filename: str) -> dict[str, str]:
    return {
        "image_filename": filename,
        "image_media_type": media_type,
        "image_base64": base64.b64encode(content).decode("ascii"),
    }


def _payload(**updates) -> dict:
    payload = {
        "title": "검색 결과 오류",
        "content": "검색 결과가 지도와 일치하지 않습니다.",
        "page_url": "https://mooncen.kr/?keyword=test",
        "user_agent": "MoonCen test browser",
        "viewport": "1440x900",
    }
    payload.update(updates)
    return payload


def _authenticated_client(monkeypatch, send_stub=None) -> TestClient:
    user = SimpleNamespace(id=uuid4(), email="reporter@mooncen.test")
    app.dependency_overrides[get_current_user] = lambda: user
    monkeypatch.setattr(
        bug_report_email,
        "send_bug_report_email",
        send_stub or (lambda **_kwargs: None),
    )
    return TestClient(app, raise_server_exceptions=False)


def _configure_mail(monkeypatch, *, security: str = "starttls") -> None:
    monkeypatch.setenv("MOONCEN_BUG_REPORT_TO", "operator@mooncen.test")
    monkeypatch.setenv("MOONCEN_BUG_REPORT_FROM", "no-reply@mooncen.test")
    monkeypatch.setenv("MOONCEN_SMTP_HOST", "smtp.mooncen.test")
    monkeypatch.setenv("MOONCEN_SMTP_PORT", "587")
    monkeypatch.setenv("MOONCEN_SMTP_USERNAME", "smtp-user")
    monkeypatch.setenv("MOONCEN_SMTP_PASSWORD", "smtp-password")
    monkeypatch.setenv("MOONCEN_SMTP_SECURITY", security)


def test_bug_report_requires_authentication():
    response = TestClient(app, raise_server_exceptions=False).post("/api/bug-reports", json=_payload())

    assert response.status_code == 401
    assert response.json() == {"detail": "Missing token"}


def test_bug_report_sends_expected_contract_and_success_message(monkeypatch):
    captured = {}

    def capture(**kwargs):
        captured.update(kwargs)

    client = _authenticated_client(monkeypatch, capture)
    response = client.post("/api/bug-reports", json=_payload())

    assert response.status_code == 200
    assert response.json() == {"message": "버그 제보가 전송되었습니다."}
    assert captured["title"] == "검색 결과 오류"
    assert captured["content"] == "검색 결과가 지도와 일치하지 않습니다."
    assert captured["page_url"].startswith("https://mooncen.kr/")
    assert captured["image"] is None
    assert captured["request_id"] != "unknown"


def test_bug_report_validates_and_passes_image(monkeypatch):
    captured = {}
    client = _authenticated_client(monkeypatch, lambda **kwargs: captured.update(kwargs))
    payload = _payload(**_image_payload(_png(), "image/png", "화면 캡처.png"))

    response = client.post("/api/bug-reports", json=payload)

    assert response.status_code == 200
    image = captured["image"]
    assert image.media_type == "image/png"
    assert (image.width, image.height) == (2, 3)
    assert image.original_filename == "화면 캡처.png"


@pytest.mark.parametrize(
    ("content", "media_type", "filename", "dimensions"),
    [
        (_jpeg(), "image/jpeg", "screen.jpg", (2, 3)),
        (_png(), "image/png", "screen.png", (2, 3)),
        (_webp(), "image/webp", "screen.webp", (2, 3)),
    ],
)
def test_supported_image_containers_are_structurally_validated(content, media_type, filename, dimensions):
    image = bug_report_email.validate_bug_report_image(
        filename=filename,
        media_type=media_type,
        data_base64=base64.b64encode(content).decode("ascii"),
    )

    assert (image.width, image.height) == dimensions
    assert image.content == content


def test_image_media_type_spoof_and_trailing_data_are_rejected():
    with pytest.raises(bug_report_email.BugReportImageError):
        bug_report_email.validate_bug_report_image(
            filename="fake.png",
            media_type="image/png",
            data_base64=base64.b64encode(_jpeg()).decode("ascii"),
        )
    with pytest.raises(bug_report_email.BugReportImageError):
        bug_report_email.validate_bug_report_image(
            filename="trailing.jpg",
            media_type="image/jpeg",
            data_base64=base64.b64encode(_jpeg() + b"not-an-image").decode("ascii"),
        )


def test_image_crc_pixel_cap_and_encoded_size_are_enforced():
    broken_crc = bytearray(_png())
    broken_crc[29] ^= 0x01
    with pytest.raises(bug_report_email.BugReportImageError, match="checksum"):
        bug_report_email.validate_bug_report_image(
            filename="bad.png",
            media_type="image/png",
            data_base64=base64.b64encode(broken_crc).decode("ascii"),
        )

    with pytest.raises(bug_report_email.BugReportImageError, match="pixel count"):
        bug_report_email.validate_bug_report_image(
            filename="huge.png",
            media_type="image/png",
            data_base64=base64.b64encode(_png(5_000, 5_000)).decode("ascii"),
        )

    with pytest.raises(bug_report_email.BugReportImageError, match="too large"):
        bug_report_email.validate_bug_report_image(
            filename="large.png",
            media_type="image/png",
            data_base64="A" * (bug_report_email.MAX_IMAGE_BASE64_CHARS + 1),
        )


def test_partial_image_and_short_normalized_text_are_rejected(monkeypatch):
    client = _authenticated_client(monkeypatch)

    assert client.post(
        "/api/bug-reports",
        json=_payload(image_filename="screen.png"),
    ).status_code == 422
    assert client.post(
        "/api/bug-reports",
        json=_payload(title="  가  "),
    ).status_code == 422
    assert client.post(
        "/api/bug-reports",
        json=_payload(content="   짧음   "),
    ).status_code == 422


def test_client_cannot_supply_mail_headers_or_recipient(monkeypatch):
    client = _authenticated_client(monkeypatch)

    response = client.post(
        "/api/bug-reports",
        json={**_payload(), "recipient": "attacker@example.test"},
    )

    assert response.status_code == 422


def test_bug_report_rate_limit_is_three_per_hour(monkeypatch):
    client = _authenticated_client(monkeypatch)

    assert [client.post("/api/bug-reports", json=_payload()).status_code for _ in range(3)] == [200, 200, 200]
    limited = client.post("/api/bug-reports", json=_payload())
    assert limited.status_code == 429
    assert limited.json() == {"detail": "Too many requests"}


@pytest.mark.parametrize(
    "failure",
    [
        bug_report_email.BugReportConfigurationError("secret configuration detail"),
        bug_report_email.BugReportDeliveryError("secret SMTP detail"),
    ],
)
def test_mail_configuration_and_delivery_failures_return_same_safe_error(monkeypatch, failure):
    def fail(**_kwargs):
        raise failure

    client = _authenticated_client(monkeypatch, fail)
    response = client.post("/api/bug-reports", json=_payload())

    assert response.status_code == 503
    assert response.json() == {"detail": "Bug report service unavailable"}
    assert "secret" not in response.text


def test_smtp_none_fails_closed_in_production(monkeypatch):
    _configure_mail(monkeypatch, security="none")
    monkeypatch.setenv("ENVIRONMENT", "production")

    with pytest.raises(bug_report_email.BugReportConfigurationError, match="Unencrypted"):
        bug_report_email.load_email_settings()


def test_missing_mail_configuration_fails_closed(monkeypatch):
    monkeypatch.setenv("MOONCEN_BUG_REPORT_TO", "operator@mooncen.test")

    with pytest.raises(bug_report_email.BugReportConfigurationError):
        bug_report_email.load_email_settings()


def test_subject_newlines_cannot_inject_mail_headers():
    settings = bug_report_email.BugReportEmailSettings(
        recipient="operator@mooncen.test",
        sender="no-reply@mooncen.test",
        host="smtp.mooncen.test",
        port=587,
        username="smtp-user",
        password="smtp-password",
        security="starttls",
    )

    message = bug_report_email.build_bug_report_message(
        settings,
        title="오류\r\nBcc: attacker@example.test",
        content="충분히 긴 버그 제보 내용입니다.",
        reporter_id="user-id",
        reporter_email="reporter@mooncen.test",
        request_id="request-id",
    )

    assert "\r" not in str(message["Subject"])
    assert "\n" not in str(message["Subject"])
    assert message.get_all("Bcc") is None
    assert message["To"] == "operator@mooncen.test"


def test_smtp_uses_starttls_login_and_fixed_envelope(monkeypatch):
    _configure_mail(monkeypatch)
    calls = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            calls.append(("connect", host, port, timeout))

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def ehlo(self):
            calls.append(("ehlo",))

        def starttls(self, *, context):
            assert context is not None
            calls.append(("starttls",))

        def login(self, username, password):
            calls.append(("login", username, password))

        def send_message(self, message: EmailMessage, *, from_addr, to_addrs):
            calls.append(("send", from_addr, to_addrs, message))

    monkeypatch.setattr(bug_report_email.smtplib, "SMTP", FakeSMTP)
    bug_report_email.send_bug_report_email(
        title="검색 오류",
        content="검색 결과가 잘못 표시되는 문제가 있습니다.",
        reporter_id="user-id",
        reporter_email="reporter@mooncen.test",
        request_id="request-id",
        image=bug_report_email.validate_bug_report_image(
            filename="screen.png",
            media_type="image/png",
            data_base64=base64.b64encode(_png()).decode("ascii"),
        ),
    )

    assert ("connect", "smtp.mooncen.test", 587, bug_report_email.SMTP_TIMEOUT_SECONDS) in calls
    assert ("starttls",) in calls
    assert ("login", "smtp-user", "smtp-password") in calls
    send_call = next(call for call in calls if call[0] == "send")
    assert send_call[1:3] == ("no-reply@mooncen.test", ["operator@mooncen.test"])
    attachments = list(send_call[3].iter_attachments())
    assert len(attachments) == 1
    assert attachments[0].get_filename() == "bug-report-request-id.png"
