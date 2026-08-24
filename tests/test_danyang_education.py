from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

from Crawler import municipal_danyang as dy


@dataclass
class Target:
    provider: str = dy.DANYANG_PROVIDER
    url: str = dy.DANYANG_CANONICAL_URL


class _Session:
    def close(self) -> None:
        pass


def _course(
    identity: str,
    *,
    current: bool,
    status: str = "신청중",
) -> dict[str, Any]:
    return {
        "term_seq": "44",
        "edu_insti_seq": "1",
        "open_course_seq": identity,
        "org_seq": "1",
        "title_list": f"여름 특별강좌 | 테스트 과정 {identity}/",
        "title_detail": f"여름 특별강좌_ 테스트 과정 {identity}",
        "capacity_total": 20,
        "capacity_current": 7,
        "apply_start": "2026-06-01" if current else "2025-06-01",
        "apply_end": "2026-06-30" if current else "2025-06-30",
        "start": "2026-07-01" if current else "2025-07-01",
        "end": "2026-08-31" if current else "2025-08-31",
        "fee": "무료",
        "status": status,
        "institution": "단양군평생학습관",
        "schedule": "[화] 10:00 ~ 12:00 ,평생학습관 202호",
        "target": "성인",
    }


class FixtureSite:
    def __init__(self) -> None:
        self.courses = [
            _course("1001", current=True),
            _course("1002", current=False, status="학습완료"),
        ]
        self.list_calls = 0
        self.detail_calls: list[str] = []
        self.drift = False
        self.bad_header = False
        self.bad_period_identity = ""
        self.preparsed = False

    @staticmethod
    def session_factory() -> _Session:
        return _Session()

    def fetcher(self, _session: Any, url: str, _timeout: int) -> Any:
        if url == dy.DANYANG_CANONICAL_URL:
            value = self._landing_html()
        elif url == dy.DANYANG_LIST_URL:
            self.list_calls += 1
            value = self._list_html(drift=self.drift and self.list_calls >= 2)
        else:
            query = parse_qs(urlparse(url).query)
            identity = query.get("open_course_seq", [""])[0]
            row = next(item for item in self.courses if item["open_course_seq"] == identity)
            self.detail_calls.append(identity)
            value = self._detail_html(row)
        return BeautifulSoup(value, "lxml") if self.preparsed else value

    @staticmethod
    def _landing_html() -> str:
        return """
        <html><head><title>단양군 평생학습관 | 단양군 평생학습관</title></head>
        <body><h2>수강 안내 및 신청</h2>
          <iframe title="수강신청"
            src="/lms/sub3/course_lst.jsp?edu_insti_seq=1&amp;org_seq=1"></iframe>
        </body></html>
        """

    def _list_html(self, *, drift: bool) -> str:
        headers = list(dy._LIST_HEADERS)
        if self.bad_header:
            headers[1] = "정원"
        head = "".join(f"<th>{value}</th>" for value in headers)
        body = "".join(self._list_row(row, drift=drift and index == 0) for index, row in enumerate(self.courses))
        if not body:
            body = '<tr><td colspan="5">등록된 강좌가 없습니다.</td></tr>'
        return f"""
        <html><head><title>프로그램 리스트</title></head><body>
          <table class="tb_list"><thead><tr>{head}</tr></thead>
            <tbody>{body}</tbody></table>
        </body></html>
        """

    @staticmethod
    def _list_row(row: dict[str, Any], *, drift: bool) -> str:
        title = row["title_list"] + (" 변경" if drift else "")
        returnurl = "null"
        menu_code = "null"
        href = (
            "/lms/menu.jsp?menu=menu_13_01_v"
            f"&term_seq={row['term_seq']}"
            f"&edu_insti_seq={row['edu_insti_seq']}"
            f"&open_course_seq={row['open_course_seq']}"
            f"&returnurl={returnurl}&menu_code={menu_code}"
            f"&org_seq={row['org_seq']}"
        )
        onclick = (
            f"openCourseView('{row['term_seq']}','{row['edu_insti_seq']}',"
            f"'{row['open_course_seq']}','{row['org_seq']}',"
            f"'{returnurl}&menu_code={menu_code}'); return false;"
        )
        period = (
            f"{row['apply_start']} 09:00~{row['apply_end']} 18:00 "
            f"{row['start']}~{row['end']}"
        )
        return f"""
        <tr>
          <td><a href="{href}" onclick="{onclick}">{title}</a></td>
          <td>{row['capacity_total']}/{row['capacity_current']}</td>
          <td>{period}</td><td>{row['fee']}</td><td>{row['status']}</td>
        </tr>
        """

    def _detail_html(self, row: dict[str, Any]) -> str:
        start = "2026-07-02" if row["open_course_seq"] == self.bad_period_identity else row["start"]
        fields = [
            ("운영기관", row["institution"]),
            ("운영기관 연락처", "043-421-7909"),
            ("교육기간/시수", f"{start} ~ {row['end']} / 8 주"),
            ("정원", f"{row['capacity_total']}명"),
            ("교육시간/장소", row["schedule"]),
            ("수강료", row["fee"]),
            ("대상", row["target"]),
            ("교육목적", "테스트 교육"),
            ("강의안내", "테스트 안내"),
            ("교재", "테스트 교재"),
            ("강의 계획서 파일", "교수이름과연락처가포함된계획서.hwp"),
        ]
        body = "".join(f"<tr><th>{key}</th><td>{value}</td></tr>" for key, value in fields)
        return f"""
        <html><head><title>단양군 평생학습관 | 단양군 평생학습관</title></head>
        <body>
          <table class="tb_view"><tr><th>교수명</th><td>비공개 테스트 강사</td></tr></table>
          <table class="tb_view">
            <tr><th class="bbs_tit" colspan="4">{row['title_detail']}</th></tr>
            {body}
          </table>
        </body></html>
        """


def _collect(site: FixtureSite, **kwargs: Any):
    return dy.collect_danyang_lifelong_courses(
        Target(),
        fetcher=site.fetcher,
        session_factory=site.session_factory,
        today="2026-07-21",
        max_workers=2,
        **kwargs,
    )


def test_target_requires_exact_provider_and_url() -> None:
    assert dy.is_danyang_target(Target())
    assert not dy.is_danyang_target(Target(provider="MUNI_OTHER"))
    assert not dy.is_danyang_target(Target(url=dy.DANYANG_CANONICAL_URL + "&page=1"))
    assert not dy.is_danyang_target(Target(url="https://user:pass@ok.danyang.go.kr/lms/menu.jsp?menu=menu_03_01"))
    assert not dy.is_danyang_target(Target(url="http://ok.danyang.go.kr/lms/menu.jsp?menu=menu_03_01"))


def test_complete_snapshot_uses_iframe_rows_and_current_details() -> None:
    site = FixtureSite()
    rows, parser, meta = _collect(site)

    assert parser == dy.DANYANG_PARSER
    assert len(rows) == 1
    assert meta["source_rows"] == 2
    assert meta["current_count"] == 1
    assert meta["detail_attempts"] == meta["detail_pages"] == 1
    assert meta["list_requests"] == 2
    assert meta["list_rechecks"] == 1
    assert meta["full_snapshot_validated"] is True
    row = rows[0]
    assert row["provider_course_id"].endswith(":44:1:1001:1")
    assert row["title"] == "여름 특별강좌_ 테스트 과정 1001"
    assert row["period"] == "2026-07-01 ~ 2026-08-31"
    assert row["apply_period"] == "2026-06-01 09:00 ~ 2026-06-30 18:00"
    assert row["status"] == "OPEN"
    assert row["reservation_available"] is True
    assert row["venue"] == "평생학습관 202호"
    assert row["capacity_total"] == 20
    assert row["capacity_current"] == 7
    assert row["raw_fields"]["detail_verified"] is True

    persisted = repr(row)
    assert "비공개 테스트 강사" not in persisted
    assert "043-421-7909" not in persisted
    assert "교수이름과연락처가포함된계획서" not in persisted
    assert "테스트 교육" not in persisted


def test_preparsed_soup_keeps_document_structure() -> None:
    site = FixtureSite()
    site.preparsed = True
    rows, _, meta = _collect(site)
    assert len(rows) == 1
    assert meta["full_snapshot_validated"] is True

def test_complete_empty_current_snapshot_is_explicit() -> None:
    site = FixtureSite()
    site.courses = [_course("1002", current=False, status="학습완료")]
    rows, _, meta = _collect(site)
    assert rows == []
    assert meta["source_rows"] == 1
    assert meta["current_count"] == 0
    assert meta["detail_attempts"] == 0
    assert meta["full_snapshot_validated"] is True
    assert meta["no_current_data"] is True
    assert "no current/future" in meta["no_current_reason"]


def test_official_no_data_marker_is_complete() -> None:
    site = FixtureSite()
    site.courses = []
    rows, _, meta = _collect(site)
    assert rows == []
    assert meta["source_rows"] == 0
    assert meta["full_snapshot_validated"] is True
    assert meta["no_current_data"] is True


def test_list_recheck_drift_fails_closed() -> None:
    site = FixtureSite()
    site.drift = True
    rows, _, meta = _collect(site)
    assert rows == []
    assert meta["full_snapshot_validated"] is False
    assert "changed during traversal" in meta["configured_collection_error"]


def test_detail_limit_fails_closed_before_detail_requests() -> None:
    site = FixtureSite()
    rows, _, meta = _collect(site, detail_limit=0)
    assert rows == []
    assert meta["source_cap_reached"] is True
    assert meta["detail_attempts"] == 0
    assert site.detail_calls == []
    assert "detail_limit" in meta["configured_collection_error"]


def test_changed_list_headers_fail_closed() -> None:
    site = FixtureSite()
    site.bad_header = True
    rows, _, meta = _collect(site)
    assert rows == []
    assert meta["full_snapshot_validated"] is False
    assert "headers changed" in meta["configured_collection_error"]


def test_unknown_source_status_fails_closed() -> None:
    site = FixtureSite()
    site.courses[0]["status"] = "새로운상태"
    rows, _, meta = _collect(site)
    assert rows == []
    assert "unknown status" in meta["configured_collection_error"]


def test_detail_period_mismatch_fails_closed() -> None:
    site = FixtureSite()
    site.bad_period_identity = "1001"
    rows, _, meta = _collect(site)
    assert rows == []
    assert meta["detail_attempts"] == 1
    assert meta["detail_pages"] == 0
    assert "detail period differs" in meta["configured_collection_error"]


def test_duplicate_source_identity_fails_closed() -> None:
    site = FixtureSite()
    site.courses.append(dict(site.courses[0]))
    rows, _, meta = _collect(site)
    assert rows == []
    assert "duplicate identities" in meta["configured_collection_error"]


def test_repository_dedupe_callback_receives_one_argument() -> None:
    site = FixtureSite()
    calls: list[list[dict[str, Any]]] = []

    def dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        calls.append(rows)
        return list(rows)

    rows, _, meta = _collect(site, dedupe_rows=dedupe)
    assert len(rows) == 1
    assert len(calls) == 1
    assert calls[0] == rows
    assert meta["full_snapshot_validated"] is True
