from __future__ import annotations

import argparse
import hashlib
import html
import re
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote_plus, urljoin

import requests
import yaml


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "config" / "museum_course_search_targets.yaml"
NAMU_URL = "https://namu.wiki/w/%EB%B0%95%EB%AC%BC%EA%B4%80/%EB%AA%A9%EB%A1%9D"

QUERY_INTENTS = [
    {
        "id": "course",
        "label": "강좌",
        "keywords": ["강좌", "강좌 신청", "수강 신청"],
    },
    {
        "id": "education",
        "label": "교육",
        "keywords": ["교육 프로그램", "교육 신청", "어린이 교육"],
    },
    {
        "id": "experience",
        "label": "체험",
        "keywords": ["체험 프로그램", "체험 신청", "주말 체험"],
    },
    {
        "id": "guided_tour",
        "label": "해설",
        "keywords": ["전시 해설", "해설 예약", "도슨트"],
    },
    {
        "id": "reservation",
        "label": "예약",
        "keywords": ["프로그램 예약", "예약 신청", "관람 예약"],
    },
]

MUSEUM_SUFFIXES = ("박물관", "미술관", "기념관", "역사관", "자료관", "전시관", "과학관")
BLOCKED_TITLE_PARTS = (
    "분류:",
    "파일:",
    "틀:",
    "목록",
    "나무위키",
    "위키백과",
)
DOMESTIC_SECTION_START = "id='s-1'"
DOMESTIC_SECTION_END = "id='s-2'"
OVERSEAS_SECTION_START = "id='s-3'"


class MuseumLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.current_heading = ""
        self._heading_tag = ""
        self._heading_parts: list[str] = []
        self.links: list[dict] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {key: value or "" for key, value in attrs}
        if tag in {"h2", "h3", "h4", "h5"}:
            self._heading_tag = tag
            self._heading_parts = []

        title = html.unescape(attr_map.get("title", "")).strip()
        href = attr_map.get("href", "")
        if tag == "a" and href.startswith("/w/") and is_museum_title(title):
            self.links.append(
                {
                    "name": title,
                    "namu_url": urljoin("https://namu.wiki", href),
                    "source_section": self.current_heading,
                }
            )

    def handle_data(self, data: str) -> None:
        if self._heading_tag:
            self._heading_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == self._heading_tag:
            heading = clean_heading("".join(self._heading_parts))
            if heading:
                self.current_heading = heading
            self._heading_tag = ""
            self._heading_parts = []


def clean_heading(value: str) -> str:
    value = html.unescape(value)
    value = re.sub(r"\[편집\]", "", value)
    value = re.sub(r"\[\d+\]", "", value)
    value = re.sub(r"^\s*\d+(?:\.\d+)*\.\s*", "", value)
    return re.sub(r"\s+", " ", value).strip()


def is_museum_title(title: str) -> bool:
    if not title or title == "박물관":
        return False
    if any(part in title for part in BLOCKED_TITLE_PARTS):
        return False
    if len(title) > 80:
        return False
    return title.endswith(MUSEUM_SUFFIXES)


def fetch_namu_page() -> str:
    response = requests.get(
        NAMU_URL,
        headers={"User-Agent": "Mozilla/5.0 MoonCenDiscovery/1.0"},
        timeout=30,
    )
    response.raise_for_status()
    return response.text


def select_article_section(html_text: str, include_overseas: bool) -> str:
    marker = html_text.find(DOMESTIC_SECTION_START)
    if marker == -1:
        return html_text
    start = html_text.rfind("<h2", 0, marker)
    if start == -1:
        start = marker

    if include_overseas:
        end = len(html_text)
    else:
        end = html_text.find(DOMESTIC_SECTION_END, start)
        if end == -1:
            end = html_text.find(OVERSEAS_SECTION_START, start)
        if end == -1:
            end = len(html_text)

    return html_text[start:end]


def extract_museums(html_text: str, include_overseas: bool) -> list[dict]:
    parser = MuseumLinkParser()
    parser.feed(select_article_section(html_text, include_overseas))

    seen: set[str] = set()
    museums: list[dict] = []
    for item in parser.links:
        name = item["name"]
        if name in seen:
            continue
        seen.add(name)
        museums.append(item)
    return museums


def google_url(query: str) -> str:
    return "https://www.google.com/search?q=" + quote_plus(query)


def provider_code(index: int, name: str) -> str:
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:8].upper()
    return f"MUSEUM_{index:04d}_{digest}"


def category_for_name(name: str) -> str:
    if name.endswith("미술관"):
        return "art_museum"
    if name.endswith("과학관"):
        return "science_museum"
    if name.endswith("기념관"):
        return "memorial_museum"
    if name.endswith("역사관"):
        return "history_museum"
    if name.endswith("자료관"):
        return "archive_museum"
    if name.endswith("전시관"):
        return "exhibition_museum"
    return "museum"


def build_queries(name: str) -> list[dict]:
    queries: list[dict] = []
    seen: set[str] = set()
    for intent in QUERY_INTENTS:
        for keyword in intent["keywords"]:
            query = f"{name} {keyword}"
            if query in seen:
                continue
            seen.add(query)
            queries.append(
                {
                    "intent": intent["id"],
                    "intent_label": intent["label"],
                    "keyword": keyword,
                    "query": query,
                    "google_search_url": google_url(query),
                }
            )
    return queries


def build_yaml(museums: list[dict], include_overseas: bool) -> dict:
    targets = []
    for index, museum in enumerate(museums, start=1):
        name = museum["name"]
        queries = build_queries(name)
        targets.append(
            {
                "provider": provider_code(index, name),
                "name": name,
                "category": category_for_name(name),
                "owner_type": "unknown",
                "region": "unknown",
                "priority": 3,
                "status": "search_seed",
                "source": "namu_wiki_museum_list",
                "source_section": museum.get("source_section") or "unknown",
                "source_url": NAMU_URL,
                "namu_url": museum["namu_url"],
                "official_url": None,
                "search_query_count": len(queries),
                "queries": queries,
                "crawler_notes": [
                    "검색 결과에서 공식 홈페이지 또는 공식 예약/교육 플랫폼을 우선 후보로 승격한다.",
                    "블로그, 뉴스, 여행 후기, 위키 문서는 후보에서 제외한다.",
                    "상세 수집 시 title, branch, address, period, schedule_raw, target, fee, status, description, image_url 필드를 우선 확인한다.",
                ],
            }
        )

    return {
        "version": 1,
        "generated_at": date.today().isoformat(),
        "source": {
            "name": "나무위키 박물관/목록",
            "url": NAMU_URL,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "section": "all" if include_overseas else "대한민국",
        },
        "scope": "나무위키 박물관/목록 기반 박물관·미술관·기념관·역사관·과학관 교육/강좌/체험/해설/예약 검색 시드",
        "collection_policy": {
            "include": [
                "공식 홈페이지 또는 공식 예약/교육 플랫폼의 강좌, 교육, 체험, 해설, 예약 프로그램",
                "박물관, 미술관, 기념관, 역사관, 과학관의 어린이/가족/성인 대상 프로그램",
                "무료/유료 교육 프로그램, 전시해설, 도슨트, 주말 체험, 단체 프로그램",
            ],
            "exclude": [
                "나무위키, 위키백과, 블로그 후기, 여행 후기, 언론 기사",
                "채용, 입찰, 일반 공지, 전시 소개만 있고 실제 신청 가능한 프로그램 목록이 없는 페이지",
                "학교 내부 공지, 민간 학원, 단순 대관/행사 안내",
            ],
            "preferred_domains": [".go.kr", ".or.kr", ".re.kr", "official museum domains"],
        },
        "query_intents": QUERY_INTENTS,
        "targets": targets,
        "summary": {
            "target_count": len(targets),
            "query_count": sum(len(target["queries"]) for target in targets),
            "include_overseas": include_overseas,
            "categories": sorted({target["category"] for target in targets}),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate museum course search YAML from Namu Wiki museum list.")
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--include-overseas", action="store_true", help="Include overseas sections after the domestic list.")
    args = parser.parse_args()

    html_text = fetch_namu_page()
    museums = extract_museums(html_text, include_overseas=args.include_overseas)
    data = build_yaml(museums, include_overseas=args.include_overseas)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=140), encoding="utf-8")
    print(f"wrote {args.out} targets={data['summary']['target_count']} queries={data['summary']['query_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
