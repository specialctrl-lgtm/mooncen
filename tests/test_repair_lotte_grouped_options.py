from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

from tools import repair_lotte_grouped_options as repair
from tools.repair_lotte_grouped_options import (
    grouped_course_ids_from_lines,
    lotte_url_from_course_id,
)


def test_grouped_course_ids_are_deduplicated_from_log_lines() -> None:
    lines = [
        "Found 6 LOTTE grouped course options: 0002-2026-2-0579",
        "prefix Found 6 LOTTE grouped course options: 0002-2026-2-0579",
        "Found 4 LOTTE grouped course options: 0013-2026-3-0386",
        "unrelated",
    ]

    assert grouped_course_ids_from_lines(lines) == {
        "0002-2026-2-0579",
        "0013-2026-3-0386",
    }


def test_lotte_grouped_course_url_is_bounded_to_official_origin() -> None:
    assert lotte_url_from_course_id("0002-2026-2-0579") == (
        "https://culture.lotteshopping.com/application/search/view.do"
        "?brchCd=0002&yy=2026&lectSmsterCd=2&lectCd=0579"
    )

    with pytest.raises(ValueError):
        lotte_url_from_course_id("../unsafe")


def test_grouped_repair_uses_parallel_batches_and_skips_known_family_members(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batches: list[list[str]] = []
    saved: list[str] = []

    class HttpSession:
        def close(self) -> None:
            return None

    class FakeCrawler:
        had_errors = False

        def __init__(self) -> None:
            self.http_session = HttpSession()
            self._existing_course_ids_by_raw_url: dict[str, str] = {}

        def _load_existing_course_ids_by_raw_url(self) -> dict[str, str]:
            return {}

        def scrape_course_details(self, course_list: list[dict]) -> list[list[dict]]:
            batches.append([item["url"] for item in course_list])
            results: list[list[dict]] = []
            for item in course_list:
                query = parse_qs(urlparse(item["url"]).query)
                lecture = query["lectCd"][0]
                family = [
                    {
                        "provider_course_id": f"0002-2026-2-{lecture}",
                        "branch_code": "0002",
                    }
                ]
                if lecture == "0001":
                    family.append(
                        {
                            "provider_course_id": "0002-2026-2-0002",
                            "branch_code": "0002",
                        }
                    )
                results.append(family)
            return results

        def save_course(self, item: dict, branch_id: str) -> bool:
            assert branch_id == "branch-id"
            saved.append(item["provider_course_id"])
            return True

        def _close_driver(self) -> None:
            return None

    monkeypatch.setattr(repair, "LotteCrawler", FakeCrawler)
    monkeypatch.setattr(
        repair,
        "load_lotte_branch_ids",
        lambda: {"0002": "branch-id"},
    )

    summary = repair.repair_grouped_options(
        {
            "0002-2026-2-0001",
            "0002-2026-2-0002",
        },
        delay_seconds=0,
        batch_size=1,
    )

    assert len(batches) == 1
    assert len(batches[0]) == 1
    assert saved == ["0002-2026-2-0001", "0002-2026-2-0002"]
    assert summary == {
        "logged_ids": 2,
        "families": 1,
        "saved": 2,
        "skipped": 0,
        "errors": 0,
    }


def test_grouped_repair_batch_size_is_bounded() -> None:
    with pytest.raises(ValueError, match="batch_size"):
        repair.repair_grouped_options(
            {"0002-2026-2-0001"},
            batch_size=0,
        )
