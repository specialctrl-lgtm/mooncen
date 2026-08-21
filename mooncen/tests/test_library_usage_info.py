from bs4 import BeautifulSoup

import json

from Crawler.library_usage_info import (
    discover_redirect_links,
    discover_usage_links,
    extract_library_usage_info,
    fetch_library_usage_info,
    start_url_candidates,
)


def test_extract_library_usage_info_from_table():
    soup = BeautifulSoup(
        """
        <main>
          <table>
            <tr><th>이용시간</th><td>평일 09:00~22:00 / 주말 09:00~18:00</td></tr>
            <tr><th>휴관일</th><td>매주 월요일 및 법정공휴일</td></tr>
          </table>
        </main>
        """,
        "lxml",
    )

    info = extract_library_usage_info(soup, "https://library.example/guide")

    assert "09:00" in info.operating_hours
    assert "월요일" in info.regular_holiday
    assert info.source_url == "https://library.example/guide"


def test_discover_usage_links_prefers_usage_guide_links():
    soup = BeautifulSoup(
        """
        <nav>
          <a href="/lecture/list.do">강좌 신청</a>
          <a href="/guide/useTime.do">이용안내</a>
          <a href="/guide/closed.do">휴관일</a>
        </nav>
        """,
        "lxml",
    )

    links = discover_usage_links(soup, "https://library.example/main")

    assert "https://library.example/guide/useTime.do" in links
    assert "https://library.example/guide/closed.do" in links
    assert all("lecture" not in link for link in links)


def test_extract_facility_usage_info_includes_admission_fee():
    soup = BeautifulSoup(
        """
        <main>
          <table>
            <tr><th>\uad00\ub78c\uc2dc\uac04</th><td>\ud654~\uc77c 10:00~18:00</td></tr>
            <tr><th>\ud734\uad00\uc77c</th><td>\ub9e4\uc8fc \uc6d4\uc694\uc77c, 1\uc6d4 1\uc77c, \uc124\ub0a0, \ucd94\uc11d</td></tr>
            <tr><th>\uad00\ub78c\ub8cc</th><td>\uc5b4\ub978 3,000\uc6d0 / \uc5b4\ub9b0\uc774 \ubb34\ub8cc</td></tr>
          </table>
        </main>
        """,
        "lxml",
    )

    info = extract_library_usage_info(soup, "https://museum.example/guide")

    assert "10:00" in info.operating_hours
    assert "\uc6d4\uc694\uc77c" in info.regular_holiday
    assert "3,000" in info.admission_fee
    assert info.as_basic_info()["admission_fee"] == info.admission_fee


def test_discover_usage_links_accepts_museum_science_usage_labels():
    soup = BeautifulSoup(
        """
        <nav>
          <a href="/program/list.do">\ud504\ub85c\uadf8\ub7a8 \uc2e0\uccad</a>
          <a href="/guide/viewing.do">\uad00\ub78c\uc548\ub0b4</a>
          <a href="/guide/fee.do">\uc774\uc6a9\uc694\uae08</a>
        </nav>
        """,
        "lxml",
    )

    links = discover_usage_links(soup, "https://science.example/main")

    assert "https://science.example/guide/viewing.do" in links
    assert "https://science.example/guide/fee.do" in links
    assert all("program" not in link for link in links)


class _FakeResponse:
    status_code = 200
    encoding = "utf-8"
    apparent_encoding = "utf-8"

    def __init__(self, text: str):
        self.text = text

    def json(self):
        return json.loads(self.text)


class _FakeSession:
    headers: dict[str, str] = {}

    def __init__(self, html):
        self.html = html
        self.requests = []

    def get(self, url: str, timeout: int, verify: bool = True, **kwargs):
        self.requests.append({"url": url, "timeout": timeout, "verify": verify, **kwargs})
        if isinstance(self.html, dict):
            return _FakeResponse(self.html[url])
        return _FakeResponse(self.html)


def test_fetch_usage_info_keeps_landing_page_usage_data_before_branch_heading():
    html = """
    <main>
      <section>
        <h2>\uad00\ub78c\uc2dc\uac04</h2>
        <p>\uc6d4/\ud654/\ubaa9/\uae08/\uc77c 09:30 ~ 17:30</p>
        <p>\uc218/\ud1a0 09:30 ~ 21:00</p>
        <h2>\uad00\ub78c\ub8cc</h2>
        <p>\ubb34\ub8cc</p>
        <p>\ud2b9\ubcc4\uc804\uc2dc\ub294 \uc720\ub8cc</p>
      </section>
      <section>
        <h2>\uad6d\ub9bd\uc911\uc559\ubc15\ubb3c\uad00</h2>
        <p>\ud604\uc7ac\uc804\uc2dc</p>
      </section>
    </main>
    """

    info = fetch_library_usage_info(
        ["https://museum.example/"],
        session=_FakeSession(html),
        timeout=1,
        max_pages=1,
        branch_name="\uad6d\ub9bd\uc911\uc559\ubc15\ubb3c\uad00",
    )

    assert "09:30" in info.operating_hours
    assert "\ubb34\ub8cc" in info.admission_fee


def test_fetch_usage_info_follows_simple_javascript_redirect():
    info = fetch_library_usage_info(
        ["https://science.example/"],
        session=_FakeSession(
            {
                "https://science.example/": """
                <html><body>
                  <script>location.href="/mps/index.do";</script>
                </body></html>
                """,
                "https://science.example/mps/index.do": """
                <main>
                  <h2>\uad00\ub78c\uc2dc\uac04</h2>
                  <p>09:30 ~ 17:30</p>
                  <p>\ud734\uad00\uc77c : \ub9e4\uc8fc \uc6d4\uc694\uc77c, \uc124\ub0a0, \ucd94\uc11d\ub2f9\uc77c</p>
                </main>
                """,
            }
        ),
        timeout=1,
        max_pages=2,
        branch_name="\uad6d\ub9bd\uc911\uc559\uacfc\ud559\uad00",
    )

    assert "09:30" in info.operating_hours
    assert "\uc6d4\uc694\uc77c" in info.regular_holiday


def test_fetch_usage_info_sends_same_site_referer():
    session = _FakeSession(
        """
        <main>
          <p>\uad00\ub78c\uc2dc\uac04 : 09:30 ~ 17:30</p>
          <p>\ud734\uad00\uc77c : \ub9e4\uc8fc \uc6d4\uc694\uc77c</p>
        </main>
        """
    )

    fetch_library_usage_info(
        ["https://science.example/tourGuide/tourGuide.do"],
        session=session,
        timeout=1,
        max_pages=1,
    )

    assert session.requests[0]["headers"]["Referer"] == "https://science.example/"


def test_start_url_candidates_preserves_detail_url_before_root():
    detail_url = "https://science.example/kor/CMS/Contents/Contents.do?mCode=MN002"

    candidates = start_url_candidates([detail_url])

    assert candidates[0] == detail_url
    assert "https://science.example/" in candidates


def test_discover_redirect_links_resolves_string_var_concatenation():
    soup = BeautifulSoup(
        """
        <html><body>
          <script>
            var url = "/";
            location.href = url + "kor/";
          </script>
        </body></html>
        """,
        "lxml",
    )

    links = discover_redirect_links(soup, "https://science.example/")

    assert links[0] == "https://science.example/kor/"


def test_extract_fee_from_multiline_table_like_text_after_label():
    soup = BeautifulSoup(
        """
        <main>
          <p>\uad00\ub78c\uc694\uae08</p>
          <p>\uad6c\ubd84</p>
          <p>\uc77c\ubc18\uc804</p>
          <p>\uac1c\uc778</p>
          <p>\ub2e8\uccb4</p>
          <p>\uc131\uc778</p>
          <p>1000\uc6d0</p>
          <p>700\uc6d0</p>
        </main>
        """,
        "lxml",
    )

    info = extract_library_usage_info(soup, "https://reservation.example/detail")

    assert "1000\uc6d0" in info.admission_fee


def test_branch_context_parking_section_falls_back_to_real_usage_info():
    soup = BeautifulSoup(
        """
        <main>
          <p>\uad00\ub78c\uc2dc\uac04</p>
          <p>\uad00\ub78c\uc2dc\uac04 : 09:30 ~ 17:30</p>
          <p>\ud734\uad00\uc77c</p>
          <p>\ud734\uad00\uc77c : \ub9e4\uc8fc \uc6d4\uc694\uc77c</p>
          <p>\uad00\ub78c\ub8cc</p>
          <p>\ub300\uc778 3,000\uc6d0</p>
          <p>\uc18c\uc778 2,000\uc6d0</p>
          <p>\uad6d\ub9bd\ub300\uad6c\uacfc\ud559\uad00 \uc8fc\ucc28\ub8cc</p>
          <p>\uc774\uc6a9\uc2dc\uac04</p>
          <p>\uc77c\ubc18\ucc28\ub7c9 2,000\uc6d0 \uc785\ucc28 : 09:00 ~ 18:00</p>
        </main>
        """,
        "lxml",
    )

    info = extract_library_usage_info(
        soup,
        "https://www.dnsm.or.kr/tourGuide/tourGuide.do",
        branch_name="\uad6d\ub9bd\ub300\uad6c\uacfc\ud559\uad00",
    )

    assert "09:30" in info.operating_hours
    assert "\uc6d4\uc694\uc77c" in info.regular_holiday
    assert "3,000\uc6d0" in info.admission_fee
    assert "\uc8fc\ucc28" not in info.operating_hours


def test_discover_usage_links_prioritizes_direct_tour_guide():
    soup = BeautifulSoup(
        """
        <nav>
          <a href="/tourGuide/facilities.do">\uc2dc\uc124\uc548\ub0b4</a>
          <a href="/tourGuide/tourGuide.do">\uc774\uc6a9\uc548\ub0b4</a>
        </nav>
        """,
        "lxml",
    )

    links = discover_usage_links(soup, "https://www.dnsm.or.kr/reservation/detail.do")

    assert links[0] == "https://www.dnsm.or.kr/tourGuide/tourGuide.do"


def test_fetch_usage_info_prioritizes_discovered_usage_links_over_seed_backlog():
    session = _FakeSession(
        {
            "https://science.example/reservation/detail1.do": """
            <main>
              <a href="/tourGuide/tourGuide.do">\uc774\uc6a9\uc548\ub0b4</a>
            </main>
            """,
            "https://science.example/": "<main></main>",
            "https://science.example/reservation/detail2.do": "<main></main>",
            "https://science.example/reservation/detail3.do": "<main></main>",
            "https://science.example/tourGuide/tourGuide.do": """
            <main>
              <p>\uad00\ub78c\uc2dc\uac04 : 09:30 ~ 17:30</p>
              <p>\ud734\uad00\uc77c : \ub9e4\uc8fc \uc6d4\uc694\uc77c</p>
              <p>\uad00\ub78c\ub8cc : \ub300\uc778 3,000\uc6d0</p>
            </main>
            """,
        }
    )

    info = fetch_library_usage_info(
        [
            "https://science.example/reservation/detail1.do",
            "https://science.example/reservation/detail2.do",
            "https://science.example/reservation/detail3.do",
        ],
        session=session,
        timeout=1,
        max_pages=4,
    )

    assert info.source_url == "https://science.example/tourGuide/tourGuide.do"
    assert "09:30" in info.operating_hours
    assert "3,000\uc6d0" in info.admission_fee


def test_fetch_usage_info_does_not_follow_app_internal_location_scripts():
    info = fetch_library_usage_info(
        [
            "https://app.example/detail.do",
            "https://app.example/guide/use.do",
        ],
        session=_FakeSession(
            {
                "https://app.example/detail.do": """
                <main>
                  <p>\uc608\uc57d \uc0c1\uc138</p>
                  <p>\ubcf8\ubb38</p><p>1</p><p>2</p><p>3</p><p>4</p><p>5</p><p>6</p><p>7</p>
                  <script>function logout(){ location.href="/logout.do"; }</script>
                </main>
                """,
                "https://app.example/": "<main></main>",
                "https://app.example/guide/use.do": """
                <main>
                  <p>\uad00\ub78c\uc2dc\uac04 : 09:30 ~ 17:30</p>
                  <p>\ud734\uad00\uc77c : \ub9e4\uc8fc \uc6d4\uc694\uc77c</p>
                </main>
                """,
            }
        ),
        timeout=1,
        max_pages=3,
    )

    assert info.source_url == "https://app.example/guide/use.do"
    assert "09:30" in info.operating_hours


def test_start_url_candidates_adds_gwacheon_science_guide_pages():
    candidates = start_url_candidates(["https://www.sciencecenter.go.kr/edu/user/edu/eduView.do?eduSeq=3873"])

    assert "https://www.sciencecenter.go.kr/gnsm/guide/private" in candidates
    assert "https://www.sciencecenter.go.kr/gnsm/guide/group" in candidates


def test_start_url_candidates_adds_branch_official_usage_pages_first():
    candidates = start_url_candidates(
        ["https://yeyak.daegu.go.kr/expr/detail/DSS_INST_00000125/MSM_PRGRM_00000236"],
        branch_name="\ubc29\uc9dc\uc720\uae30\ubc15\ubb3c\uad00",
    )

    assert candidates[0] == "https://www.dmhm.or.kr/bangjja/content.html?md=0156"


def test_fetch_usage_info_stops_on_complete_branch_official_page():
    official_url = "https://www.dmhm.or.kr/bangjja/content.html?md=0156"
    info = fetch_library_usage_info(
        ["https://yeyak.daegu.go.kr/expr/detail/DSS_INST_00000125/MSM_PRGRM_00000236"],
        branch_name="\ubc29\uc9dc\uc720\uae30\ubc15\ubb3c\uad00",
        session=_FakeSession(
            {
                official_url: """
                <main>
                  <table>
                    <tr><th>\uad00\ub78c\uc2dc\uac04</th><td>9:00 ~ 18:00 (17:30\uae4c\uc9c0 \uc785\uc7a5)</td></tr>
                    <tr><th>\ud734\uad00\uc77c</th><td>1.1, \uc124\ub0a0 \ubc0f \ucd94\uc11d\ub2f9\uc77c, \ub9e4\uc8fc \uc6d4\uc694\uc77c</td></tr>
                    <tr><th>\uad00\ub78c\ub8cc</th><td>\ubb34\ub8cc</td></tr>
                  </table>
                </main>
                """,
            }
        ),
        timeout=1,
        max_pages=3,
    )

    assert info.source_url == official_url
    assert info.visited_urls == [official_url]
    assert "\ubb34\ub8cc" in info.admission_fee


def test_extract_usage_info_accepts_gwangju_literature_holiday_wording():
    soup = BeautifulSoup(
        """
        <main>
          <p>\ud734 \uad00 \uc77c : 1\uc6d4 1\uc77c, \uba85\uc808\uc5f0\ud734, \uad00\uacf5\uc11c\uc758 \uacf5\ud734\uc77c \ub2e4\uc74c\ub0a0, \uadf8\ubc16\uc5d0 \uc2dc\uc7a5\uc774 \ud544\uc694\ud558\ub2e4\uace0 \uc815\ud558\ub294 \ud734\uad00\uc77c</p>
        </main>
        """,
        "lxml",
    )

    info = extract_library_usage_info(soup, "https://www.gwangju.go.kr/gjlm/contentsView.do?pageId=gjlm16")

    assert "1\uc6d4 1\uc77c" in info.regular_holiday
    assert "\uba85\uc808\uc5f0\ud734" in info.regular_holiday


def test_extract_usage_info_accepts_combined_admission_and_parking_fee_label():
    soup = BeautifulSoup(
        """
        <main>
          <p>\uc785\uc7a5\ub8cc \ubc0f \uc8fc\ucc28\ub8cc</p>
          <p>\ubb34\ub8cc(\ub2e8, \uccb4\ud5d8\ub8cc \ubcc4\ub3c4)</p>
        </main>
        """,
        "lxml",
    )

    info = extract_library_usage_info(soup, "http://www.gn.go.kr/solhyang/contents.do?key=846")

    assert "\ubb34\ub8cc" in info.admission_fee


def test_fetch_ansan_gamgol_usage_info_from_spa_bundle():
    root_url = "https://lib.ansan.go.kr/"
    app_url = "https://lib.ansan.go.kr/app.fake.js"
    functions = ['function(){var t=this;return t._v("unused")}'] * 20
    functions[18] = (
        'function(){var t=this;return [t._v("\ud734\uad00\uc77c"),'
        't._v("\uc815\uae30\ud734\uad00\uc77c"),t._v("\ub9e4\uc8fc \uae08\uc694\uc77c"),'
        't._v("\uae30\ud0c0\ud734\uad00\uc77c"),t._v("\uad6d\uacbd\uc77c, \uc815\ubd80\uc5d0\uc11c \uc9c0\uc815\ud558\ub294 \uacf5\ud734\uc77c")] }'
    )
    functions[19] = (
        'function(){var t=this;return [t._v("\uc6b4\uc601\uc2dc\uac04"),'
        't._v("\uc2e4\ubcc4"),t._v("\uc774\uc6a9\uc2dc\uac04"),t._v("\uc815\uae30\ud734\uad00\uc77c"),'
        't._v("\ubb38\ud5cc\uc790\ub8cc\uc2e4"),t._v("\ud3c9\uc77c"),t._v("09:00~22:00"),'
        't._v("\uc6b4\uc601\uc548\ud568"),t._v("\ud1a0/\uc77c\uc694\uc77c"),t._v("09:00~18:00")] }'
    )
    script_text = '1340:function(t,e,s){"use strict";var a=1,r=[' + ",".join(functions) + "],o=1}"

    info = fetch_library_usage_info(
        ["https://lib.ansan.go.kr/gamgol"],
        branch_name="\uac10\uace8\ub3c4\uc11c\uad00",
        session=_FakeSession(
            {
                root_url: f'<html><script src="{app_url}"></script></html>',
                app_url: script_text,
            }
        ),
        timeout=1,
        max_pages=1,
    )

    assert "\ub9e4\uc8fc \uae08\uc694\uc77c" in info.regular_holiday
    assert "09:00~22:00" in info.operating_hours
    assert "\uc815\uae30\ud734\uad00\uc77c" not in info.operating_hours
