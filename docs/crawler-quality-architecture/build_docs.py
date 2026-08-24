#!/usr/bin/env python3
"""Build crawler-quality diagrams and DOCX files from the editable Markdown/SVG sources.

The repository already contains a tested Markdown-to-Word renderer in
docs/architecture-review/build_docs.py.  This entry point reuses that renderer
and supplies crawler-quality-specific sources and native Pillow diagram
fallbacks.  Application and deployment code are not touched.
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets"
BASE_PATH = ROOT.parent / "architecture-review" / "build_docs.py"
SOURCE_FILES = [
    ROOT / "MoonCen_크롤러_품질_아키텍처_간략본.md",
    ROOT / "MoonCen_크롤러_품질_아키텍처_상세본.md",
    ROOT / "MoonCen_크롤러_개선방안_우선순위_간략본.md",
    ROOT / "MoonCen_크롤러_개선방안_우선순위_상세본.md",
    ROOT / "MoonCen_크롤러_품질_운영_가이드.md",
]
SNAPSHOT = "2026-08-19 UTC"
REVISION = "master@8d55e873bfb06ec33f566839fce7ee98650955f8"


def load_base_builder():
    if not BASE_PATH.is_file():
        raise FileNotFoundError(f"Shared Word renderer is missing: {BASE_PATH}")
    spec = importlib.util.spec_from_file_location("mooncen_architecture_doc_builder", BASE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load shared Word renderer")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ROOT = ROOT
    module.ASSETS = ASSETS
    module.SOURCE_FILES = SOURCE_FILES
    module.SNAPSHOT = SNAPSHOT
    module.REVISION = REVISION
    return module


B = load_base_builder()


def canvas(height: int, title: str, subtitle: str):
    return B.base_canvas(height, title, subtitle)


def render_feedback(path: Path) -> None:
    image, draw = canvas(
        930,
        "Crawler Quality Feedback Loop",
        "Evidence-backed, replayable and reversible rule changes",
    )
    draw.rounded_rectangle((45, 125, 1555, 440), radius=24, fill="#FFFFFF", outline="#D7E0E8", width=3)
    draw.text((75, 150), "DIAGNOSE & DESIGN", font=B.image_font(18, True), fill="#476782")
    boxes = [
        ((72, 215, 312, 365), "1. Quality Signal", ["issue, drift, run error", "[triage; not truth]"], "E8F1FF", "5F8ECB"),
        ((377, 215, 617, 365), "2. Frozen Evidence", ["HTML / JSON + trace", "[redacted and hashed]"], "FFF4DF", "D29B3D"),
        ((682, 215, 922, 365), "3. Reviewed Labels", ["record and field truth", "[append-only gold set]"], "EAF7F2", "4F9B7A"),
        ((987, 215, 1227, 365), "4. Safe Rule Draft", ["extract / classify / cover", "[typed DSL; no code]"], "EDE9FF", "7868B4"),
        ((1292, 215, 1532, 365), "5. Offline Replay", ["baseline vs candidate", "[networkless diff]"], "EDF7FB", "5791AA"),
    ]
    for bounds, title, lines, fill, outline in boxes:
        B.draw_box(draw, bounds, title, lines, fill=fill, outline=outline, title_size=17, line_size=14)
    for left, right in ((312, 377), (617, 682), (922, 987), (1227, 1292)):
        B.draw_arrow(draw, [(left, 290), (right, 290)])

    draw.rounded_rectangle((45, 485, 1555, 800), radius=24, fill="#FFFFFF", outline="#D7E0E8", width=3)
    draw.text((75, 510), "RELEASE & LEARN", font=B.image_font(18, True), fill="#476782")
    lower = [
        ((90, 575, 365, 725), "6. Review & Artifact", ["independent approval", "canonical SHA"], "FFF4DF", "D29B3D"),
        ((465, 575, 740, 725), "7. Staging Canary", ["signed rule + adapter", "no primary write"], "EDE9FF", "7868B4"),
        ((840, 575, 1115, 725), "8. Controlled Promote", ["existing fingerprint gate", "safe close-missing"], "EAF7F2", "4F9B7A"),
        ((1215, 575, 1490, 725), "9. Monitor & Learn", ["truth metrics + proxies", "cases feed the loop"], "E8F1FF", "5F8ECB"),
    ]
    for bounds, title, lines, fill, outline in lower:
        B.draw_box(draw, bounds, title, lines, fill=fill, outline=outline, title_size=18, line_size=14)
    for left, right in ((365, 465), (740, 840), (1115, 1215)):
        B.draw_arrow(draw, [(left, 650), (right, 650)])
    B.draw_arrow(draw, [(1412, 365), (1412, 465), (227, 465), (227, 575)])
    B.draw_arrow(draw, [(1350, 725), (1350, 835), (190, 835), (190, 365)])
    B.draw_arrow(draw, [(1350, 725), (1350, 770), (602, 770), (602, 725)], color="A94442", dashed=True)
    draw.rounded_rectangle((350, 860, 1250, 905), radius=12, fill="#17324D")
    B.draw_centered(
        draw,
        (800, 883),
        "No label -> no metric claim | No replay -> no approval | Incomplete snapshot -> no close",
        16,
        B.WHITE,
        True,
    )
    image.save(path, "PNG", optimize=True)


def render_components(path: Path) -> None:
    image, draw = canvas(
        980,
        "Target Component Architecture",
        "Extend current control, staging and promotion boundaries with a quality evidence plane",
    )
    draw.rounded_rectangle((45, 125, 1555, 320), radius=24, fill="#FFFFFF", outline="#D7E0E8", width=3)
    draw.text((75, 150), "EXPERIENCE PLANE", font=B.image_font(18, True), fill="#476782")
    top = [
        ((75, 200, 380, 285), "Quality Inbox", ["signals, cases, SLA"], "E8F1FF", "5F8ECB"),
        ((455, 200, 760, 285), "Evidence & Labels", ["raw, candidate, fields"], "EAF7F2", "4F9B7A"),
        ((835, 200, 1140, 285), "Guided Rule Builder", ["forms + validated YAML"], "EDE9FF", "7868B4"),
        ((1215, 200, 1520, 285), "Replay / Release", ["diff, gates, rollback"], "EDF7FB", "5791AA"),
    ]
    for bounds, title, lines, fill, outline in top:
        B.draw_box(draw, bounds, title, lines, fill=fill, outline=outline, title_size=18, line_size=14)

    draw.rounded_rectangle((45, 360, 1555, 665), radius=24, fill="#FFFFFF", outline="#D7E0E8", width=3)
    draw.text((75, 385), "QUALITY CONTROL PLANE - ENVIRONMENT BOUND", font=B.image_font(18, True), fill="#476782")
    mid = [
        ((75, 450, 385, 595), "Ops Quality API", ["RBAC, audit, revision fence", "cases, rules, replay"], "EAF7F2", "4F9B7A"),
        ((455, 450, 765, 595), "Control PostgreSQL", ["append-only metadata", "review and assignment"], "FFF4DF", "D29B3D"),
        ((835, 450, 1145, 595), "Evidence Object Store", ["redacted HTML / JSON", "digest, ACL, retention"], "EDE9FF", "7868B4"),
        ((1215, 450, 1525, 595), "Replay Sandbox", ["networkless, read-only", "bounded resources"], "EDF7FB", "5791AA"),
    ]
    for bounds, title, lines, fill, outline in mid:
        B.draw_box(draw, bounds, title, lines, fill=fill, outline=outline, title_size=18, line_size=14)
    for left, right in ((385, 455), (765, 835), (1145, 1215)):
        B.draw_arrow(draw, [(left, 523), (right, 523)])
    for x in (227, 607, 987, 1367):
        B.draw_arrow(draw, [(x, 320), (x, 450)])

    draw.rounded_rectangle((45, 700, 1555, 900), radius=24, fill="#FFFFFF", outline="#D7E0E8", width=3)
    draw.text((75, 725), "DATA PLANE - REUSE CURRENT FAIL-CLOSED PATH", font=B.image_font(18, True), fill="#476782")
    bottom = [
        ((75, 780, 335, 865), "Source Adapters", ["transport / paging"], "EDE9FF", "7868B4"),
        ((385, 780, 645, 865), "Rule Runtime", ["signed, pinned rule"], "E8F1FF", "5F8ECB"),
        ((695, 780, 955, 865), "Crawl Staging", ["normalized snapshots"], "FFF4DF", "D29B3D"),
        ((1005, 780, 1265, 865), "Promotion Gate", ["reviewed fingerprint"], "FDEEED", "C55F55"),
        ((1315, 780, 1525, 865), "Primary DB", ["courses / branches"], "EAF7F2", "4F9B7A"),
    ]
    for bounds, title, lines, fill, outline in bottom:
        B.draw_box(draw, bounds, title, lines, fill=fill, outline=outline, title_size=17, line_size=13)
    for left, right in ((335, 385), (645, 695), (955, 1005), (1265, 1315)):
        B.draw_arrow(draw, [(left, 823), (right, 823)])
    B.draw_arrow(draw, [(1367, 595), (1367, 675), (515, 675), (515, 780)], color="A94442", dashed=True)
    draw.text(
        (65, 950),
        "Initial rollout uses the current legacy + staging path. Distributed workers are not an MVP prerequisite.",
        font=B.image_font(14),
        fill="#56657A",
    )
    image.save(path, "PNG", optimize=True)


def render_pipeline(path: Path) -> None:
    image, draw = canvas(
        930,
        "Deterministic Rule Evaluation Pipeline",
        "Every keep, drop and abstain carries rule identity, reason and source evidence",
    )
    draw.rounded_rectangle((45, 125, 1555, 320), radius=24, fill="#FFFFFF", outline="#D7E0E8", width=3)
    draw.text((75, 150), "CODE-MANAGED ADAPTER BOUNDARY", font=B.image_font(18, True), fill="#476782")
    adapters = [
        ((75, 205, 385, 285), "Fetch / Authenticate", ["session, TLS, budget"], "EDE9FF", "7868B4"),
        ((455, 205, 765, 285), "Traverse / Paginate", ["cursor, browser, detail"], "EDE9FF", "7868B4"),
        ((835, 205, 1145, 285), "Freeze Raw Evidence", ["response + manifest"], "EDE9FF", "7868B4"),
        ((1215, 205, 1525, 285), "Document Stream", ["bounded input"], "FFF4DF", "D29B3D"),
    ]
    for bounds, title, lines, fill, outline in adapters:
        B.draw_box(draw, bounds, title, lines, fill=fill, outline=outline, title_size=18, line_size=13)
    for left, right in ((385, 455), (765, 835), (1145, 1215)):
        B.draw_arrow(draw, [(left, 245), (right, 245)])

    draw.rounded_rectangle((45, 360, 1555, 710), radius=24, fill="#FFFFFF", outline="#D7E0E8", width=3)
    draw.text((75, 385), "DATA-MANAGED RULE PACK - TYPED AND ALLOWLISTED", font=B.image_font(18, True), fill="#476782")
    stages = [
        ((70, 455, 285, 565), "1. Extract", ["CSS / JSON path", "field provenance"], "E8F1FF", "5F8ECB"),
        ((320, 455, 535, 565), "2. Normalize", ["date / fee / text", "pure transforms"], "EDF7FB", "5791AA"),
        ((570, 455, 785, 565), "3. Classify", ["include / exclude", "stable reason"], "EAF7F2", "4F9B7A"),
        ((820, 455, 1035, 565), "4. Validate", ["semantic + fields", "fail closed"], "FFF4DF", "D29B3D"),
        ((1070, 455, 1285, 565), "5. Identity", ["dedupe / key", "collision evidence"], "EDE9FF", "7868B4"),
        ((1320, 455, 1535, 565), "6. Coverage", ["count / sentinel", "snapshot contract"], "FDEEED", "C55F55"),
    ]
    for bounds, title, lines, fill, outline in stages:
        B.draw_box(draw, bounds, title, lines, fill=fill, outline=outline, title_size=17, line_size=13)
    for left, right in ((285, 320), (535, 570), (785, 820), (1035, 1070), (1285, 1320)):
        B.draw_arrow(draw, [(left, 510), (right, 510)])
    draw.rounded_rectangle((150, 615, 1450, 675), radius=14, fill="#17324D")
    B.draw_centered(
        draw,
        (800, 645),
        "snapshot SHA | adapter version | rule revision | stage | rule id | decision | reason | evidence",
        16,
        B.WHITE,
        True,
    )
    B.draw_arrow(draw, [(1370, 285), (1370, 340), (177, 340), (177, 455)])

    outputs = [
        ((75, 770, 385, 845), "Kept Candidates", ["staging input"], "EAF7F2", "4F9B7A"),
        ((455, 770, 765, 845), "Dropped / Quarantine", ["review evidence"], "FFF4DF", "D29B3D"),
        ((835, 770, 1145, 845), "Coverage Evidence", ["complete or hold"], "E8F1FF", "5F8ECB"),
        ((1215, 770, 1525, 845), "Gate Result", ["approve or block"], "FDEEED", "C55F55"),
    ]
    for bounds, title, lines, fill, outline in outputs:
        B.draw_box(draw, bounds, title, lines, fill=fill, outline=outline, title_size=18, line_size=13)
    for x in (230, 610, 990, 1370):
        B.draw_arrow(draw, [(800, 675), (800, 735), (x, 735), (x, 770)])
    draw.text(
        (65, 900),
        "Precedence: global safety -> family -> provider -> target -> expiring emergency. Conflicts fail compilation.",
        font=B.image_font(14),
        fill="#56657A",
    )
    image.save(path, "PNG", optimize=True)


def render_diagram_with_pillow(stem: str, path: Path) -> None:
    renderers = {
        "01-quality-feedback-loop": render_feedback,
        "02-component-architecture": render_components,
        "03-rule-evaluation-pipeline": render_pipeline,
    }
    try:
        renderer = renderers[stem]
    except KeyError as exc:
        raise ValueError(f"No crawler-quality Pillow renderer registered for {stem}") from exc
    renderer(path)


def write_checksums() -> Path:
    paths = [
        *SOURCE_FILES,
        *(source.with_suffix(".docx") for source in SOURCE_FILES),
        *sorted(ASSETS.glob("*.svg")),
        *sorted(ASSETS.glob("*.png")),
        ROOT / "README.md",
        ROOT / "index.html",
        ROOT / "build_docs.py",
        ROOT / "validate_deliverables.py",
        ROOT / "requirements-docs.txt",
    ]
    lines = []
    for path in sorted(paths, key=lambda item: item.relative_to(ROOT).as_posix()):
        if not path.is_file():
            raise FileNotFoundError(path)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {path.relative_to(ROOT).as_posix()}")
    output = ROOT / "SHA256SUMS"
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output


def main() -> int:
    B.render_diagram_with_pillow = render_diagram_with_pillow
    result = B.main()
    checksums = write_checksums()
    print(f"Wrote checksums: {checksums.name}")
    return result


if __name__ == "__main__":
    sys.exit(main())
