from __future__ import annotations

import re
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {
    ".git",
    ".pytest_cache",
    "_organized",
    "dist",
    "node_modules",
    "playwright-report",
    "test-results",
    "venv",
    "venv_clean",
}
SKIP_FILES = {"deploy.local.ps1"}
TEXT_SUFFIXES = {
    ".cfg",
    ".conf",
    ".env",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".jsx",
    ".log",
    ".md",
    ".ps1",
    ".py",
    ".service",
    ".sh",
    ".sql",
    ".timer",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}
SECRET_PATTERNS = {
    "private key": re.compile("-----BEGIN " + r"(?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "Google API key": re.compile(r"AI" + r"za[0-9A-Za-z_-]{30,}"),
    "OpenAI-style key": re.compile(r"(?<![A-Za-z0-9])sk" + r"-[A-Za-z0-9_-]{20,}"),
    "GitHub token": re.compile(r"gh" + r"[pousr]_[A-Za-z0-9]{20,}"),
    "Slack token": re.compile(r"xo" + r"x[baprs]-[A-Za-z0-9-]{20,}"),
    "Telegram bot URL": re.compile(r"/bot" + r"[0-9]{8,12}:[A-Za-z0-9_-]{30,}/"),
    "Cloudflare tunnel token": re.compile(r"eyJ" + r"hIjoi[A-Za-z0-9_-]{40,}"),
    "Tailscale auth URL": re.compile(r"https://login\.tailscale\.com/a/[A-Za-z0-9_-]{8,}"),
}


def _active_files():
    for directory, child_dirs, filenames in os.walk(ROOT, topdown=True, followlinks=False):
        child_dirs[:] = [
            name
            for name in child_dirs
            if name not in SKIP_DIRS
            and not name.startswith(("venv_", ".venv"))
        ]
        base = Path(directory)
        for filename in filenames:
            if filename in SKIP_FILES:
                continue
            path = base / filename
            relative = path.relative_to(ROOT)
            if path.name.startswith(".env") and path.name != ".env.example":
                continue
            yield path, relative


def test_repository_contains_no_private_key_or_database_dump_files() -> None:
    forbidden: list[str] = []
    for path, relative in _active_files():
        if path.suffix.lower() in {".key", ".pem", ".p12", ".pfx", ".dump"}:
            forbidden.append(str(relative))
    assert forbidden == []


def test_repository_text_contains_no_high_confidence_secret_patterns() -> None:
    findings: list[str] = []
    for path, relative in _active_files():
        if path.suffix.lower() not in TEXT_SUFFIXES and path.name != ".env.example":
            continue
        if path.stat().st_size > 5_000_000:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for label, pattern in SECRET_PATTERNS.items():
            if pattern.search(text):
                findings.append(f"{relative}: {label}")
    assert findings == []


def test_openai_key_pattern_rejects_keys_without_matching_public_ask_slugs() -> None:
    pattern = SECRET_PATTERNS["OpenAI-style key"]
    assert pattern.search("OPENAI_API_KEY=" + "sk" + "-" + "a" * 24)
    assert pattern.search("/column/a" + "sk" + "-" + "public-information-slug") is None


def test_cross_platform_control_files_are_normalized_to_lf() -> None:
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    for pattern in (
        "*.py text eol=lf",
        "*.yml text eol=lf",
        "*.sql text eol=lf",
        "*.sh text eol=lf",
        "*.service text eol=lf",
        "*.timer text eol=lf",
        "*.ps1 text eol=lf",
    ):
        assert pattern in attributes


def test_retired_one_off_prompt_launchers_do_not_return_to_repository_root() -> None:
    retired = (
        "script.ps1",
        "script1.ps1",
        "mooncen_codex_script.md",
        "operation-console-codex-prompt.md",
    )
    assert [name for name in retired if (ROOT / name).exists()] == []
