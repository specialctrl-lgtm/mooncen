from bs4 import BeautifulSoup

from Crawler.Crawler_Lotte import extract_lotte_description_from_soup
from Crawler.reception_period import extract_reception_period
from description_cleaner import clean_lotte_apply_period_raw, clean_lotte_description_text


def test_lotte_description_strips_order_ui_prefix():
    raw = (
        "접수 접수기간 2026.04.22~2026.07.01 문의처 031-8036-2403~4 "
        "대기 신청하기 재료비/대여료 선택 옵션선택 옵션정보 옵션선택 "
        "[8주중도] 이수윤의 마일드&빈야사 요가 (목) 90,000 원 총 주문 금액 "
        "90,000 원 장바구니 대기신청 강좌소개 "
        "< 이수윤의 마일드&빈야사 요가 > 요가 수련을 통해 몸과 마음의 균형을 찾습니다."
    )

    cleaned = clean_lotte_description_text(raw)

    assert cleaned == "< 이수윤의 마일드&빈야사 요가 > 요가 수련을 통해 몸과 마음의 균형을 찾습니다."
    assert "접수기간" not in cleaned
    assert "옵션선택" not in cleaned
    assert "장바구니" not in cleaned


def test_lotte_description_prefers_intro_container_over_popup_ui():
    html = """
    <div class="course_info">
      <div class="course_popup">접수 접수기간 2026.04.22~2026.07.01 옵션선택 총 주문 금액 장바구니 대기신청</div>
      <div class="flow_txt_area">
        <div class="anchor_con info_img_inner">
          <p class="sub_tit f_h2">강좌소개</p>
          <div class="info_img_txt">
            <p class="img"><img src="" alt=""></p>
            <div class="txt_box">&lt; 요가 &gt;<br>몸과 마음을 편안하게 하는 수업입니다.</div>
          </div>
        </div>
      </div>
    </div>
    """

    cleaned = extract_lotte_description_from_soup(BeautifulSoup(html, "html.parser"))

    assert cleaned == "< 요가 > 몸과 마음을 편안하게 하는 수업입니다."
    assert "총 주문 금액" not in cleaned


def test_lotte_description_trims_application_notice_tail():
    raw = (
        "상상력이 자라는 아이키친 키즈쿠킹입니다. "
        "■ 수강신청시 주의사항 1. 본 강좌는 [수강신청하기-재료비 옵션선택] 후 "
        "재료비를 함께 결제하여야 수강신청이 완료됩니다."
    )

    assert clean_lotte_description_text(raw) == "상상력이 자라는 아이키친 키즈쿠킹입니다."


def test_lotte_description_drops_notice_only_text():
    raw = (
        "■ 수강신청시 주의사항 1. 본 강좌는 [수강신청하기-재료비 옵션선택] 후 "
        "재료비를 함께 결제하여야 수강신청이 완료됩니다."
    )

    assert clean_lotte_description_text(raw) is None


def test_lotte_apply_period_raw_strips_detail_page_tail():
    raw = (
        "접수 접수기간 2026.04.22~2026.07.01 문의처 031-8036-2403~4 "
        "대기 신청하기 재료비/대여료 선택 옵션선택 옵션정보 옵션선택 "
        "[8주중도] 이수윤의 마일드&빈야사 요가 (목) 90,000 원 총 주문 금액 "
        "90,000 원 장바구니 대기신청 강좌소개 < 이수윤의 마일드&빈야사 요가 >"
    )

    parsed = extract_reception_period(raw)

    assert parsed["apply_period_raw"] == "2026.04.22~2026.07.01"
    assert parsed["apply_start"].isoformat() == "2026-04-22"
    assert parsed["apply_end"].isoformat() == "2026-07-01"
    assert clean_lotte_apply_period_raw(raw) == "2026.04.22~2026.07.01"


def test_lotte_reception_period_ignores_status_and_title_age_dates():
    raw = (
        "접수중 특강 [8/25] 자연재료 촉감놀이 [8-14개월+보호자1인] "
        "강의기간 2026.08.25 ~ 2026.08.25 "
        "접수기간 2026.07.23~2026.08.25 문의처 02-2218-2760"
    )

    parsed = extract_reception_period(raw)

    assert parsed["apply_start"].isoformat() == "2026-07-23"
    assert parsed["apply_end"].isoformat() == "2026-08-25"
    assert parsed["apply_period_raw"] == "2026.07.23~2026.08.25"
