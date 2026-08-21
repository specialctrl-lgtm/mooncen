#!/usr/bin/env python3
"""Validate the crawler-quality Markdown, diagrams and generated Word files."""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
import zipfile
from pathlib import Path

from defusedxml import ElementTree as ET
from PIL import Image, ImageStat


ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets"
STEMS = (
    "MoonCen_크롤러_품질_아키텍처_간략본",
    "MoonCen_크롤러_품질_아키텍처_상세본",
    "MoonCen_크롤러_개선방안_우선순위_간략본",
    "MoonCen_크롤러_개선방안_우선순위_상세본",
    "MoonCen_크롤러_품질_운영_가이드",
)
DIAGRAMS = (
    "01-quality-feedback-loop",
    "02-component-architecture",
    "03-rule-evaluation-pipeline",
)
FORBIDDEN_PLACEHOLDERS = re.compile(r"\b(?:TODO|TBD|FIXME)\b", re.IGNORECASE)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def validate_markdown(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    require(text.startswith("# "), f"{path.name}: missing H1")
    require(not FORBIDDEN_PLACEHOLDERS.search(text), f"{path.name}: unresolved placeholder")
    require(len(text) >= 2_000, f"{path.name}: unexpectedly short")
    for relative in re.findall(r"!\[[^\]]*\]\(([^\)]+)\)", text):
        target = (ROOT / relative).resolve()
        require(target.is_file(), f"{path.name}: missing image {relative}")


def validate_svg(path: Path) -> None:
    root = ET.parse(path).getroot()
    require(root.tag.endswith("svg"), f"{path.name}: root is not SVG")
    require(int(root.attrib.get("width", "0")) >= 1_200, f"{path.name}: SVG width too small")
    require(int(root.attrib.get("height", "0")) >= 700, f"{path.name}: SVG height too small")
    tags = {node.tag.rsplit("}", 1)[-1] for node in root.iter()}
    require("title" in tags and "desc" in tags, f"{path.name}: accessibility title/desc missing")


def validate_png(path: Path) -> None:
    with Image.open(path) as image:
        require(image.width >= 1_200 and image.height >= 700, f"{path.name}: PNG is too small")
        rgb = image.convert("RGB")
        stat = ImageStat.Stat(rgb.resize((64, 64)))
        require(max(stat.var) > 100, f"{path.name}: PNG appears blank")
        colors = rgb.resize((128, 128)).getcolors(maxcolors=16_384)
        require(colors is not None and len(colors) >= 12, f"{path.name}: insufficient visual variation")


def validate_docx(path: Path, expected_title: str) -> None:
    require(path.stat().st_size >= 25_000, f"{path.name}: DOCX unexpectedly small")
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        for required in ("[Content_Types].xml", "word/document.xml", "docProps/core.xml"):
            require(required in names, f"{path.name}: missing {required}")
        document = archive.read("word/document.xml").decode("utf-8")
        core = archive.read("docProps/core.xml").decode("utf-8")
        require(expected_title in core or expected_title in document, f"{path.name}: title missing")
        require("MoonCen" in document or "MOONCEN" in document, f"{path.name}: cover missing")
        require("w:updateFields" in archive.read("word/settings.xml").decode("utf-8"), f"{path.name}: TOC update flag missing")


def verify_checksums(path: Path) -> None:
    require(path.is_file(), "SHA256SUMS missing")
    entries = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, relative = line.split("  ", 1)
        target = ROOT / relative
        require(target.is_file(), f"checksum target missing: {relative}")
        actual = hashlib.sha256(target.read_bytes()).hexdigest()
        require(actual == digest, f"checksum mismatch: {relative}")
        entries += 1
    require(entries >= 20, "too few checksum entries")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-checksums", action="store_true")
    args = parser.parse_args()

    for stem in STEMS:
        markdown = ROOT / f"{stem}.md"
        docx = ROOT / f"{stem}.docx"
        require(markdown.is_file(), f"missing {markdown.name}")
        require(docx.is_file(), f"missing {docx.name}")
        validate_markdown(markdown)
        title = markdown.read_text(encoding="utf-8").splitlines()[0][2:].strip()
        validate_docx(docx, title)

    for stem in DIAGRAMS:
        svg = ASSETS / f"{stem}.svg"
        png = ASSETS / f"{stem}.png"
        require(svg.is_file(), f"missing {svg.name}")
        require(png.is_file(), f"missing {png.name}")
        validate_svg(svg)
        validate_png(png)

    if not args.skip_checksums:
        verify_checksums(ROOT / "SHA256SUMS")

    print("Validated 5 Markdown, 5 DOCX, 3 SVG and 3 PNG deliverables.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (AssertionError, OSError, ValueError, zipfile.BadZipFile, ET.ParseError) as exc:
        print(f"Validation failed: {exc}", file=sys.stderr)
        sys.exit(1)
