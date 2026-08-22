from __future__ import annotations

import unittest

from bs4 import BeautifulSoup

from Crawler.Crawler_MunicipalYaml import (
    CrawlTarget,
    MunicipalDbWriter,
    enrich_suwon_resident_autonomy_detail,
    suwon_resident_autonomy_branch_name,
    suwon_resident_autonomy_row,
)


class SuwonResidentAutonomyTests(unittest.TestCase):
    def test_dong_name_is_promoted_to_resident_center_branch(self) -> None:
        target = CrawlTarget(
            provider="MUNI_PALDAL_SUWON_GO_KR_D78BD1B4",
            name="수원시 팔달구 교육프로그램 접수",
            branch="수원시",
            url="https://paldal.suwon.go.kr/edu/lmth/02_gorp/gorp_list.asp?sbd_room=4",
            source="test",
            priority=1,
            region="경기도 수원시",
            extra={
                "branch_address": "경기도 수원시 팔달구 고등로 37",
                "branch_lat": 37.2734739,
                "branch_lon": 127.0011276,
                "branch_address_source": "PALDAL_RESIDENT_CENTER_DIRECTORY",
                "branch_coordinate_source": "ARCGIS_GEOCODING_OFFICIAL_ADDRESS",
                "branch_location_confidence": 90,
                "branch_location_verified": True,
            },
        )
        row = suwon_resident_autonomy_row(
            target,
            target.url,
            {
                "동명": "고등동",
                "강좌명": "민요교실",
                "교육시간": "수",
                "수강료": "60,000원",
                "신청/교육기간": "26.03.09(월)~26.03.13(금)",
                "접수/모집": "20/20",
                "상태": "마감",
            },
            "https://paldal.suwon.go.kr/edu/lmth/02_gorp/view.asp?bd_seqn=20260304184209",
        )

        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["branch"], "고등동 주민자치센터")
        self.assertEqual(row["venue_name"], "고등동 주민자치센터")
        self.assertEqual(row["venue_address"], "경기도 수원시 팔달구 고등로 37")
        self.assertEqual(row["branch_lat"], 37.2734739)
        self.assertEqual(row["branch_lon"], 127.0011276)
        self.assertEqual(row["capacity_current"], 20)
        self.assertEqual(row["capacity_total"], 20)

        branch = MunicipalDbWriter(target.provider).branch_info_from_row(row)
        self.assertEqual(branch["address"], "경기도 수원시 팔달구 고등로 37")
        self.assertEqual(branch["lat"], 37.2734739)
        self.assertEqual(branch["lon"], 127.0011276)
        self.assertEqual(branch["location_confidence"], 90)
        self.assertTrue(branch["location_verified"])

    def test_existing_center_name_is_not_duplicated(self) -> None:
        self.assertEqual(
            suwon_resident_autonomy_branch_name("고등동 주민자치센터"),
            "고등동 주민자치센터",
        )

    def test_detail_separates_course_and_application_periods(self) -> None:
        row = {
            "provider": "MUNI_PALDAL_SUWON_GO_KR_D78BD1B4",
            "raw_fields": {"parser": "suwon_resident_autonomy_table"},
        }
        soup = BeautifulSoup(
            """
            <table>
              <tr><th>주민센터</th><td>고등동 주민자치센터</td></tr>
              <tr><th>교육장소</th><td>고등동 행정복지센터 프로그램1실</td></tr>
              <tr><th>교육기간</th><td>26.04.01(수)~26.06.30(화)</td></tr>
              <tr><th>교육요일/시간</th><td>월(10:20 ~ 12:20)</td></tr>
              <tr><th>접수기간</th><td>2026.03.09 09:00 ~ 2026.03.13 16:00</td></tr>
              <tr><th>계좌번호</th><td>123-456-7890</td></tr>
            </table>
            """,
            "html.parser",
        )

        enrich_suwon_resident_autonomy_detail(row, soup)

        self.assertEqual(row["branch"], "고등동 주민자치센터")
        self.assertEqual(row["venue_name"], "고등동 행정복지센터 프로그램1실")
        self.assertEqual(row["period"], "2026-04-01 ~ 2026-06-30")
        self.assertEqual(row["apply_period"], "2026-03-09 ~ 2026-03-13")
        self.assertEqual(row["schedule_raw"], "월(10:20 ~ 12:20)")
        self.assertNotIn("계좌번호", row["raw_fields"]["detail"])


if __name__ == "__main__":
    unittest.main()
