from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from email.message import EmailMessage
from email.headerregistry import Address
from email.utils import formatdate, make_msgid
import os
import re
import smtplib
import ssl
import zlib


MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_BASE64_CHARS = ((MAX_IMAGE_BYTES + 2) // 3) * 4
MAX_IMAGE_PIXELS = 20_000_000
MAX_IMAGE_DIMENSION = 16_384
SMTP_TIMEOUT_SECONDS = 10

_MEDIA_TYPE_EXTENSIONS = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}
_JPEG_SOF_MARKERS = {
    0xC0,
    0xC1,
    0xC2,
    0xC3,
    0xC5,
    0xC6,
    0xC7,
    0xC9,
    0xCA,
    0xCB,
    0xCD,
    0xCE,
    0xCF,
}


class BugReportConfigurationError(RuntimeError):
    """The server is not safely configured to deliver bug reports."""


class BugReportDeliveryError(RuntimeError):
    """The configured mail server did not accept a bug report."""


class BugReportImageError(ValueError):
    """The supplied attachment is not an allowed, structurally valid image."""


@dataclass(frozen=True)
class BugReportEmailSettings:
    recipient: str
    sender: str
    host: str
    port: int
    username: str
    password: str
    security: str


@dataclass(frozen=True)
class ValidatedBugReportImage:
    content: bytes
    media_type: str
    extension: str
    width: int
    height: int
    original_filename: str


def _is_production() -> bool:
    return os.getenv("ENVIRONMENT", "dev").strip().lower() in {"prod", "production"}


def _configured_address(env_name: str) -> str:
    value = os.getenv(env_name, "").strip()
    if not value or len(value) > 254 or not value.isascii() or any(char in value for char in "\r\n\0"):
        raise BugReportConfigurationError(f"{env_name} is not configured")
    try:
        address = Address(addr_spec=value)
    except (TypeError, ValueError):
        raise BugReportConfigurationError(f"{env_name} is invalid") from None
    if not address.username or not address.domain or address.addr_spec != value:
        raise BugReportConfigurationError(f"{env_name} is invalid")
    return value


def load_email_settings() -> BugReportEmailSettings:
    recipient = _configured_address("MOONCEN_BUG_REPORT_TO")
    sender = _configured_address("MOONCEN_BUG_REPORT_FROM")
    host = os.getenv("MOONCEN_SMTP_HOST", "").strip()
    username = os.getenv("MOONCEN_SMTP_USERNAME", "").strip()
    password = os.getenv("MOONCEN_SMTP_PASSWORD", "")
    security = os.getenv("MOONCEN_SMTP_SECURITY", "starttls").strip().lower()

    if (
        not host
        or len(host) > 253
        or any(char.isspace() for char in host)
        or any(char in host for char in "/\\\0")
    ):
        raise BugReportConfigurationError("MOONCEN_SMTP_HOST is not configured")
    if not username or len(username) > 320 or any(char in username for char in "\r\n\0"):
        raise BugReportConfigurationError("MOONCEN_SMTP_USERNAME is not configured")
    if not password or len(password) > 4096 or "\0" in password:
        raise BugReportConfigurationError("MOONCEN_SMTP_PASSWORD is not configured")
    if security not in {"starttls", "ssl", "none"}:
        raise BugReportConfigurationError("MOONCEN_SMTP_SECURITY is invalid")
    if security == "none" and _is_production():
        raise BugReportConfigurationError("Unencrypted SMTP is disabled in production")

    raw_port = os.getenv("MOONCEN_SMTP_PORT", "587").strip()
    try:
        port = int(raw_port)
    except ValueError:
        raise BugReportConfigurationError("MOONCEN_SMTP_PORT is invalid") from None
    if port < 1 or port > 65_535:
        raise BugReportConfigurationError("MOONCEN_SMTP_PORT is invalid")

    return BugReportEmailSettings(
        recipient=recipient,
        sender=sender,
        host=host,
        port=port,
        username=username,
        password=password,
        security=security,
    )


def _validate_dimensions(width: int, height: int) -> tuple[int, int]:
    if width < 1 or height < 1:
        raise BugReportImageError("Image dimensions must be positive")
    if width > MAX_IMAGE_DIMENSION or height > MAX_IMAGE_DIMENSION:
        raise BugReportImageError("Image dimensions are too large")
    if width * height > MAX_IMAGE_PIXELS:
        raise BugReportImageError("Image pixel count is too large")
    return width, height


def _png_dimensions(data: bytes) -> tuple[int, int]:
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise BugReportImageError("Invalid PNG signature")

    offset = 8
    chunk_count = 0
    width = height = 0
    saw_ihdr = saw_plte = saw_idat = saw_iend = False
    idat_ended = False
    idat_bytes = 0
    while offset < len(data):
        chunk_count += 1
        if chunk_count > 10_000 or offset + 12 > len(data):
            raise BugReportImageError("Invalid PNG structure")
        length = int.from_bytes(data[offset : offset + 4], "big")
        chunk_type = data[offset + 4 : offset + 8]
        if (
            len(chunk_type) != 4
            or not all(65 <= value <= 90 or 97 <= value <= 122 for value in chunk_type)
            or 97 <= chunk_type[2] <= 122
        ):
            raise BugReportImageError("Invalid PNG chunk type")
        chunk_end = offset + 12 + length
        if chunk_end > len(data):
            raise BugReportImageError("Truncated PNG chunk")
        chunk_data = data[offset + 8 : offset + 8 + length]
        expected_crc = int.from_bytes(data[offset + 8 + length : chunk_end], "big")
        actual_crc = zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF
        if expected_crc != actual_crc:
            raise BugReportImageError("Invalid PNG checksum")

        if not saw_ihdr:
            if chunk_type != b"IHDR" or length != 13:
                raise BugReportImageError("PNG must start with IHDR")
            width = int.from_bytes(chunk_data[0:4], "big")
            height = int.from_bytes(chunk_data[4:8], "big")
            bit_depth, color_type, compression, filter_method, interlace = chunk_data[8:13]
            valid_depths = {
                0: {1, 2, 4, 8, 16},
                2: {8, 16},
                3: {1, 2, 4, 8},
                4: {8, 16},
                6: {8, 16},
            }
            if (
                bit_depth not in valid_depths.get(color_type, set())
                or compression != 0
                or filter_method != 0
                or interlace not in {0, 1}
            ):
                raise BugReportImageError("Unsupported PNG header")
            saw_ihdr = True
        elif chunk_type == b"IHDR":
            raise BugReportImageError("Duplicate PNG IHDR")

        if chunk_type[0] <= 90 and chunk_type not in {b"IHDR", b"PLTE", b"IDAT", b"IEND"}:
            raise BugReportImageError("Unknown critical PNG chunk")

        if chunk_type in {b"acTL", b"fcTL", b"fdAT"}:
            raise BugReportImageError("Animated PNG is not supported")
        if chunk_type == b"PLTE":
            if saw_plte or saw_idat or length < 3 or length > 768 or length % 3:
                raise BugReportImageError("Invalid PNG palette")
            saw_plte = True
        if chunk_type == b"IDAT":
            if idat_ended:
                raise BugReportImageError("PNG IDAT chunks must be consecutive")
            saw_idat = True
            idat_bytes += length
        elif saw_idat and chunk_type != b"IEND":
            idat_ended = True
        if chunk_type == b"IEND":
            if length != 0 or not saw_idat or idat_bytes < 1 or chunk_end != len(data):
                raise BugReportImageError("Invalid PNG ending")
            saw_iend = True
            offset = chunk_end
            break
        offset = chunk_end

    if not saw_ihdr or not saw_idat or not saw_iend or offset != len(data):
        raise BugReportImageError("Incomplete PNG image")
    return _validate_dimensions(width, height)


def _jpeg_dimensions(data: bytes) -> tuple[int, int]:
    if len(data) < 4 or data[:2] != b"\xff\xd8":
        raise BugReportImageError("Invalid JPEG signature")

    offset = 2
    width = height = 0
    saw_frame = saw_scan = saw_eoi = False
    saw_entropy = False
    in_scan = False
    marker_count = 0

    while offset < len(data):
        marker_count += 1
        if marker_count > 100_000:
            raise BugReportImageError("Invalid JPEG structure")

        if in_scan:
            marker_start = data.find(b"\xff", offset)
            if marker_start < 0 or marker_start + 1 >= len(data):
                raise BugReportImageError("JPEG scan is not terminated")
            marker_offset = marker_start + 1
            while marker_offset < len(data) and data[marker_offset] == 0xFF:
                marker_offset += 1
            if marker_offset >= len(data):
                raise BugReportImageError("JPEG scan is not terminated")
            marker = data[marker_offset]
            if marker_start > offset:
                saw_entropy = True
            if marker == 0x00 or 0xD0 <= marker <= 0xD7:
                if marker == 0x00:
                    saw_entropy = True
                offset = marker_offset + 1
                continue
            offset = marker_start
            in_scan = False
            continue

        if data[offset] != 0xFF:
            raise BugReportImageError("Invalid JPEG marker")
        marker_offset = offset + 1
        while marker_offset < len(data) and data[marker_offset] == 0xFF:
            marker_offset += 1
        if marker_offset >= len(data):
            raise BugReportImageError("Truncated JPEG marker")
        marker = data[marker_offset]
        offset = marker_offset + 1

        if marker == 0xD9:
            if offset != len(data):
                raise BugReportImageError("JPEG contains trailing data")
            saw_eoi = True
            break
        if marker in {0x00, 0xD8, 0x01} or 0xD0 <= marker <= 0xD7:
            raise BugReportImageError("Unexpected JPEG marker")
        if offset + 2 > len(data):
            raise BugReportImageError("Truncated JPEG segment")
        segment_length = int.from_bytes(data[offset : offset + 2], "big")
        if segment_length < 2 or offset + segment_length > len(data):
            raise BugReportImageError("Invalid JPEG segment length")
        payload = data[offset + 2 : offset + segment_length]

        if marker in _JPEG_SOF_MARKERS:
            if len(payload) < 6 or payload[0] not in {8, 12, 16}:
                raise BugReportImageError("Invalid JPEG frame")
            component_count = payload[5]
            if component_count not in {1, 2, 3, 4} or len(payload) != 6 + (3 * component_count):
                raise BugReportImageError("Invalid JPEG frame components")
            frame_height = int.from_bytes(payload[1:3], "big")
            frame_width = int.from_bytes(payload[3:5], "big")
            if saw_frame and (frame_width != width or frame_height != height):
                raise BugReportImageError("Conflicting JPEG dimensions")
            width, height = frame_width, frame_height
            saw_frame = True
        if marker == 0xDA:
            scan_components = payload[0] if payload else 0
            if (
                not saw_frame
                or scan_components < 1
                or scan_components > 4
                or len(payload) != 1 + (2 * scan_components) + 3
            ):
                raise BugReportImageError("Invalid JPEG scan header")
            saw_scan = True
            in_scan = True
        offset += segment_length

    if not saw_frame or not saw_scan or not saw_entropy or not saw_eoi:
        raise BugReportImageError("Incomplete JPEG image")
    return _validate_dimensions(width, height)


def _webp_dimensions(data: bytes) -> tuple[int, int]:
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise BugReportImageError("Invalid WebP signature")
    if int.from_bytes(data[4:8], "little") != len(data) - 8:
        raise BugReportImageError("Invalid WebP container size")

    offset = 12
    chunk_count = 0
    canvas_dimensions: tuple[int, int] | None = None
    frame_dimensions: tuple[int, int] | None = None
    saw_animation = False
    while offset < len(data):
        chunk_count += 1
        if chunk_count > 10_000 or offset + 8 > len(data):
            raise BugReportImageError("Invalid WebP structure")
        chunk_type = data[offset : offset + 4]
        chunk_size = int.from_bytes(data[offset + 4 : offset + 8], "little")
        payload_start = offset + 8
        payload_end = payload_start + chunk_size
        padded_end = payload_end + (chunk_size & 1)
        if padded_end > len(data):
            raise BugReportImageError("Truncated WebP chunk")
        if chunk_size & 1 and data[payload_end] != 0:
            raise BugReportImageError("Invalid WebP padding")
        payload = data[payload_start:payload_end]

        if not all(32 <= value <= 126 for value in chunk_type):
            raise BugReportImageError("Invalid WebP chunk type")

        if chunk_type == b"VP8X":
            if (
                canvas_dimensions is not None
                or offset != 12
                or chunk_size != 10
                or payload[0] & 0xC1
                or any(payload[1:4])
            ):
                raise BugReportImageError("Invalid WebP extended header")
            if payload[0] & 0x02:
                saw_animation = True
            width = 1 + int.from_bytes(payload[4:7], "little")
            height = 1 + int.from_bytes(payload[7:10], "little")
            canvas_dimensions = _validate_dimensions(width, height)
        elif chunk_type == b"VP8 ":
            if frame_dimensions is not None or len(payload) < 10:
                raise BugReportImageError("Invalid WebP lossy frame")
            frame_tag = int.from_bytes(payload[0:3], "little")
            first_partition_size = frame_tag >> 5
            if (
                frame_tag & 1
                or ((frame_tag >> 1) & 0x07) > 3
                or not ((frame_tag >> 4) & 1)
                or first_partition_size > len(payload) - 10
                or payload[3:6] != b"\x9d\x01\x2a"
            ):
                raise BugReportImageError("Invalid WebP lossy frame")
            width = int.from_bytes(payload[6:8], "little") & 0x3FFF
            height = int.from_bytes(payload[8:10], "little") & 0x3FFF
            frame_dimensions = _validate_dimensions(width, height)
        elif chunk_type == b"VP8L":
            if frame_dimensions is not None or len(payload) <= 5 or payload[0] != 0x2F:
                raise BugReportImageError("Invalid WebP lossless frame")
            packed = int.from_bytes(payload[1:5], "little")
            if (packed >> 29) & 0x07:
                raise BugReportImageError("Unsupported WebP lossless version")
            width = 1 + (packed & 0x3FFF)
            height = 1 + ((packed >> 14) & 0x3FFF)
            frame_dimensions = _validate_dimensions(width, height)
        elif chunk_type in {b"ANIM", b"ANMF"}:
            saw_animation = True

        offset = padded_end

    if offset != len(data) or frame_dimensions is None or saw_animation:
        raise BugReportImageError("Incomplete or animated WebP image")
    if canvas_dimensions is not None and frame_dimensions != canvas_dimensions:
        raise BugReportImageError("WebP frame does not match its canvas")
    return canvas_dimensions or frame_dimensions


def validate_bug_report_image(
    *,
    filename: str,
    media_type: str,
    data_base64: str,
) -> ValidatedBugReportImage:
    normalized_media_type = media_type.strip().lower()
    if normalized_media_type not in _MEDIA_TYPE_EXTENSIONS:
        raise BugReportImageError("Unsupported image media type")
    if not filename.strip() or len(filename) > 255 or any(char in filename for char in "\r\n\0"):
        raise BugReportImageError("Invalid image filename")
    if not data_base64 or len(data_base64) > MAX_IMAGE_BASE64_CHARS:
        raise BugReportImageError("Image is empty or too large")
    if not data_base64.isascii() or re.search(r"\s", data_base64):
        raise BugReportImageError("Image base64 is invalid")
    try:
        content = base64.b64decode(data_base64, validate=True)
    except (binascii.Error, ValueError):
        raise BugReportImageError("Image base64 is invalid") from None
    if not content or len(content) > MAX_IMAGE_BYTES:
        raise BugReportImageError("Image is empty or too large")

    parsers = {
        "image/jpeg": _jpeg_dimensions,
        "image/png": _png_dimensions,
        "image/webp": _webp_dimensions,
    }
    width, height = parsers[normalized_media_type](content)
    return ValidatedBugReportImage(
        content=content,
        media_type=normalized_media_type,
        extension=_MEDIA_TYPE_EXTENSIONS[normalized_media_type],
        width=width,
        height=height,
        original_filename=filename.strip(),
    )


def _single_line(value: str, *, max_length: int) -> str:
    cleaned = "".join(
        " " if ord(char) < 32 or ord(char) == 127 else char
        for char in str(value or "")
    )
    return " ".join(cleaned.split())[:max_length]


def build_bug_report_message(
    settings: BugReportEmailSettings,
    *,
    title: str,
    content: str,
    reporter_id: str,
    reporter_email: str,
    request_id: str,
    page_url: str | None = None,
    user_agent: str | None = None,
    viewport: str | None = None,
    image: ValidatedBugReportImage | None = None,
) -> EmailMessage:
    safe_title = _single_line(title, max_length=120) or "제목 없음"
    safe_request_id = re.sub(r"[^A-Za-z0-9-]", "", request_id)[:64] or "unknown"
    body_lines = [
        f"제목: {safe_title}",
        f"제보 ID: {safe_request_id}",
        f"제보자 계정 ID: {_single_line(reporter_id, max_length=64)}",
        f"제보자 이메일: {_single_line(reporter_email, max_length=254)}",
        f"페이지: {_single_line(page_url or '', max_length=2048) or '-'}",
        f"브라우저: {_single_line(user_agent or '', max_length=512) or '-'}",
        f"화면 크기: {_single_line(viewport or '', max_length=64) or '-'}",
        "",
        "내용:",
        content.strip(),
    ]
    if image is not None:
        body_lines.extend(
            [
                "",
                (
                    "첨부 이미지: "
                    f"{_single_line(image.original_filename, max_length=255)} "
                    f"({image.width}x{image.height}, {image.media_type})"
                ),
            ]
        )

    message = EmailMessage()
    message["Subject"] = f"[문센 버그제보] {safe_title}"
    message["From"] = Address(display_name="문센", addr_spec=settings.sender)
    message["To"] = Address(addr_spec=settings.recipient)
    message["Date"] = formatdate(localtime=False)
    sender_domain = settings.sender.rsplit("@", 1)[-1]
    message["Message-ID"] = make_msgid(domain=sender_domain)
    message.set_content("\n".join(body_lines), subtype="plain", charset="utf-8")
    if image is not None:
        subtype = image.media_type.split("/", 1)[1]
        message.add_attachment(
            image.content,
            maintype="image",
            subtype=subtype,
            filename=f"bug-report-{safe_request_id}.{image.extension}",
        )
    return message


def send_bug_report_email(
    *,
    title: str,
    content: str,
    reporter_id: str,
    reporter_email: str,
    request_id: str,
    page_url: str | None = None,
    user_agent: str | None = None,
    viewport: str | None = None,
    image: ValidatedBugReportImage | None = None,
) -> None:
    settings = load_email_settings()
    message = build_bug_report_message(
        settings,
        title=title,
        content=content,
        reporter_id=reporter_id,
        reporter_email=reporter_email,
        request_id=request_id,
        page_url=page_url,
        user_agent=user_agent,
        viewport=viewport,
        image=image,
    )
    context = ssl.create_default_context()
    try:
        if settings.security == "ssl":
            client_context = smtplib.SMTP_SSL(
                settings.host,
                settings.port,
                timeout=SMTP_TIMEOUT_SECONDS,
                context=context,
            )
        else:
            client_context = smtplib.SMTP(settings.host, settings.port, timeout=SMTP_TIMEOUT_SECONDS)
        with client_context as client:
            client.ehlo()
            if settings.security == "starttls":
                client.starttls(context=context)
                client.ehlo()
            client.login(settings.username, settings.password)
            client.send_message(
                message,
                from_addr=settings.sender,
                to_addrs=[settings.recipient],
            )
    except (OSError, smtplib.SMTPException, TimeoutError):
        raise BugReportDeliveryError("SMTP delivery failed") from None
