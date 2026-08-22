#!/usr/bin/env python3
"""Render the allowlisted browser configuration as an atomic JavaScript file."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path


PUBLIC_ENV_FIELDS = (
    ("MOONCEN_SITE_URL", "siteUrl"),
    ("MOONCEN_OAUTH_REDIRECT_URI", "oauthRedirectUri"),
    ("MOONCEN_KAKAO_MAPS_JAVASCRIPT_KEY", "kakaoMapsJavaScriptKey"),
    ("MOONCEN_GOOGLE_OAUTH_CLIENT_ID", "googleOAuthClientId"),
    ("MOONCEN_NAVER_OAUTH_CLIENT_ID", "naverOAuthClientId"),
)
MAX_PUBLIC_VALUE_LENGTH = 4096
MAX_ENVIRONMENT_BYTES = 64 * 1024
OUTPUT_MODE = 0o644
ENVIRONMENT_KEY_PATTERN = re.compile(r"\A[A-Z][A-Z0-9_]*\Z")
ROOT_UID = 0


class RuntimeConfigError(RuntimeError):
    """Raised when public configuration cannot be rendered safely."""


def environment_file(path: Path) -> dict[str, str]:
    try:
        metadata = path.lstat()
        payload = path.read_bytes()
    except OSError as exc:
        raise RuntimeConfigError("Runtime environment file cannot be read.") from exc
    private_user_file = (
        metadata.st_uid == os.getuid()
        and stat.S_IMODE(metadata.st_mode) == 0o600
    )
    protected_system_file = (
        metadata.st_uid == ROOT_UID
        and metadata.st_gid == os.getgid()
        and stat.S_IMODE(metadata.st_mode) == 0o640
    )
    if (
        path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or not (private_user_file or protected_system_file)
        or len(payload) > MAX_ENVIRONMENT_BYTES
    ):
        raise RuntimeConfigError("Runtime environment file is unsafe.")
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise RuntimeConfigError("Runtime environment is not valid UTF-8.") from exc
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if (
            separator != "="
            or ENVIRONMENT_KEY_PATTERN.fullmatch(key) is None
            or key in values
            or any(character in value for character in "\x00\r\n")
        ):
            raise RuntimeConfigError("Runtime environment has an invalid assignment.")
        values[key] = value
    return values


def _public_value(name: str, value: object) -> str:
    if not isinstance(value, str):
        raise RuntimeConfigError(f"{name} must be a string.")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise RuntimeConfigError(f"{name} contains a control character.")
    normalized = value.strip()
    if len(normalized) > MAX_PUBLIC_VALUE_LENGTH:
        raise RuntimeConfigError(f"{name} exceeds the public value length limit.")
    return normalized


def public_config(environ: Mapping[str, str]) -> dict[str, str]:
    """Return only the public browser fields explicitly allowed by this contract."""

    return {
        output_name: _public_value(environment_name, environ.get(environment_name, ""))
        for environment_name, output_name in PUBLIC_ENV_FIELDS
    }


def render_javascript(config: Mapping[str, str]) -> str:
    expected_fields = {output_name for _, output_name in PUBLIC_ENV_FIELDS}
    if set(config) != expected_fields:
        raise RuntimeConfigError("Runtime configuration fields do not match the allowlist.")
    normalized = {output_name: _public_value(output_name, config[output_name]) for _, output_name in PUBLIC_ENV_FIELDS}
    payload = json.dumps(
        normalized,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    # The file is loaded as an external script, but escaping HTML-significant
    # characters also keeps it safe if its contents are inspected or embedded.
    payload = payload.replace("&", r"\u0026").replace("<", r"\u003c").replace(">", r"\u003e")
    return (
        f'(function () {{\n  "use strict";\n  window.__MOONCEN_RUNTIME_CONFIG__ = Object.freeze({payload});\n}}());\n'
    )


def _fsync_directory(directory: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    descriptor = os.open(directory, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_atomic(output: Path, content: str) -> None:
    parent = output.parent
    if not parent.is_dir():
        raise RuntimeConfigError(f"Output directory does not exist: {parent}")
    if output.is_symlink() or (output.exists() and not stat.S_ISREG(output.lstat().st_mode)):
        raise RuntimeConfigError(f"Output path is not a regular file: {output}")

    payload = content.encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(
        dir=parent,
        prefix=f".{output.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, OUTPUT_MODE)
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output)
        _fsync_directory(parent)
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)
        raise


def render_to_file(output: Path, environ: Mapping[str, str]) -> None:
    write_atomic(output, render_javascript(public_config(environ)))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render MoonCen's allowlisted public browser configuration.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Existing-directory destination for runtime-config.js.",
    )
    parser.add_argument(
        "--environment-file",
        type=Path,
        help="Private installed environment source; defaults to the process environment.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        environ = (
            os.environ
            if arguments.environment_file is None
            else environment_file(arguments.environment_file)
        )
        render_to_file(arguments.output, environ)
    except (OSError, RuntimeConfigError) as exc:
        print(f"runtime config render failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
