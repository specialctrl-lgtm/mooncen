from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

import pytest

import Crawler.Crawler_GeneratedYamlTargets as engine
from Crawler.Crawler_MunicipalYaml import (
    CrawlTarget,
    ProviderReport,
    collect_national_museum_of_korea,
    suwon_library_capacity,
)
from tools.generate_registry_crawler_files import write_wrappers
from tools.validate_generated_yaml_crawlers import validate
from utils.outbound_http import SafeSession


def make_target(provider: str = "TEST_PROVIDER", url: str = "https://example.com/courses") -> CrawlTarget:
    return CrawlTarget(
        provider=provider,
        name=provider,
        branch="Test branch",
        url=url,
        source="test",
        priority=1,
        extra={"crawler_status": "ready"},
    )


def test_suwon_library_waitlist_applicants_do_not_exceed_normalized_capacity() -> None:
    assert suwon_library_capacity("신청자수 : 29 / 25") == (25, 25, 29)
    assert suwon_library_capacity("신청자수 : 8 / 25") == (25, 8, 8)
    assert suwon_library_capacity("신청자 정보 없음") == (None, None, None)


def test_modu_museum_empty_current_catalogue_is_not_a_crawler_failure(monkeypatch) -> None:
    class EmptyResponse:
        text = "<html><script>var totalPages = 1;</script><ul id='listUl'></ul></html>"

        @staticmethod
        def raise_for_status() -> None:
            return None

    class EmptySession:
        @staticmethod
        def get(*_args, **_kwargs):
            return EmptyResponse()

    monkeypatch.setattr(
        "Crawler.Crawler_MunicipalYaml.session",
        lambda: EmptySession(),
    )
    target = CrawlTarget(
        provider="NATIONAL_MUSEUM_OF_KOREA",
        name="국립광주박물관",
        branch="국립광주박물관",
        url="https://modu.museum.go.kr/learn?museum=3",
        source="test",
    )

    rows, parser, meta = collect_national_museum_of_korea(
        target,
        timeout=1,
        max_pages=3,
        detail_limit=3,
    )

    assert rows == []
    assert parser == "modu_learn_list+detail"
    assert meta["no_current_data"] is True
    assert meta["no_current_reason"] == "no_current_modu_museum_programs"


def test_experience_collection_merges_configured_and_main_site_results(monkeypatch) -> None:
    municipal = engine.municipal_yaml_module
    target = make_target(url="https://museum.example.test/program/list")
    target.extra.update(
        {
            "discover_from_main_url": True,
            "main_url": "https://museum.example.test/",
        }
    )
    configured_row = {
        "provider": target.provider,
        "title": "Configured lecture",
        "branch": target.branch,
        "raw_url": target.url,
    }
    main_row = {
        "provider": target.provider,
        "title": "Main-site performance",
        "branch": target.branch,
        "raw_url": "https://museum.example.test/performance/1",
    }
    monkeypatch.setattr(
        municipal,
        "collect_from_url",
        lambda *_args, **_kwargs: (
            [configured_row],
            "configured_parser",
            {"pages": 1, "detail_pages": 1, "discovered_links": 2},
        ),
    )
    monkeypatch.setattr(
        municipal,
        "crawl_experience_main_site",
        lambda *_args, **_kwargs: (
            [main_row],
            {"generic_card"},
            {
                "main_discovery_pages": 2,
                "main_candidate_pages": 1,
                "main_discovered_links": 3,
                "main_discovery_complete": True,
                "detail_pages": 1,
            },
        ),
    )

    rows, parser, meta = municipal.crawl_experience_from_url(
        target,
        timeout=5,
        max_depth=1,
        max_pages=2,
        detail_limit=3,
    )

    assert [row["title"] for row in rows] == ["Configured lecture", "Main-site performance"]
    assert parser == "configured_parser+generic_card"
    assert meta["main_discovery_pages"] == 2
    assert meta["detail_pages"] == 2
    assert meta["discovered_links"] == 5
    assert meta["main_discovery_complete"] is True


def test_direct_generated_experience_target_also_enables_main_discovery() -> None:
    item = {
        "provider": "DIRECT_EXPERIENCE",
        "name": "Experience center",
        "branch": "Experience center",
        "service_group": "체험",
        "url": "https://experience.example.test/program/list",
        "source": "test",
        "priority": 1,
    }

    target = engine.to_crawl_target(item)

    assert target.extra["discover_from_main_url"] is True
    assert target.extra["main_url"] == "https://experience.example.test/"


def test_experience_metadata_fills_only_missing_required_display_fields() -> None:
    target = make_target()
    target.extra.update(
        {
            "service_group": "\uccb4\ud5d8",
            "service_group_policy": "locked",
            "domain_category": "\uccb4\ud5d8\u00b7\uacac\ud559",
        }
    )
    rows = [
        {
            "provider": target.provider,
            "title": "\uc0c1\uc124 \uccb4\ud5d8",
            "branch": "\uccb4\ud5d8\uad00",
            "target": "",
            "fee": "",
            "period": "",
            "venue_name": "",
            "category": "",
            "schedule_raw": "",
        }
    ]

    engine.apply_target_metadata(rows, target)

    assert rows[0]["target"] == "\uc804\uccb4"
    assert rows[0]["fee"] == "\uc694\uae08 \ubcc4\ub3c4 \uc548\ub0b4"
    assert rows[0]["period"] == "\uc77c\uc815 \ubcc4\ub3c4 \uc548\ub0b4"
    assert rows[0]["venue_name"] == "\uccb4\ud5d8\uad00"
    assert rows[0]["category"] == "\uccb4\ud5d8\u00b7\uacac\ud559"
    assert rows[0]["schedule_raw"] == "\uc2dc\uac04 \ubcc4\ub3c4 \uc548\ub0b4"


def test_experience_metadata_uses_the_final_content_based_service_group() -> None:
    target = make_target(provider="ULSAN_EDU_BOOKING")
    target.extra.update(
        {
            "service_group": "\uacf5\uacf5\uac15\uc88c",
            "domain_category": "\ud3c9\uc0dd\ud559\uc2b5",
            "source_group": "lifelong_learning",
        }
    )
    rows = [
        {
            "provider": target.provider,
            "title": "\uc5ec\ub984\ubc29\ud559 \uc218\ud559\ucea0\ud504",
            "branch": "\uc6b8\uc0b0 \uad50\uc721\uccb4\ud5d8\uad00",
            "category": "\ud3c9\uc0dd\uad50\uc721",
            "raw_url": "https://example.com/courses/1",
            "service_group": "\uacf5\uacf5\uac15\uc88c",
            "fee": "",
            "period": "2099-08-01",
            "schedule_raw": "10:00~12:00",
            "target": "\uc804\uccb4",
            "venue_name": "\uccb4\ud5d8\uad00",
        }
    ]

    engine.apply_target_metadata(rows, target)

    assert rows[0]["fee"] == "\uc694\uae08 \ubcc4\ub3c4 \uc548\ub0b4"


def test_gwangmyeong_mixed_owner_preserves_locked_experience_row_metadata() -> None:
    target = make_target(provider="MUNI_SUGANG_GM_GO_KR_F136DD19")
    target.extra.update(
        {
            "collection_category": "공공예약",
            "domain_category": "교육·강좌",
            "source_group": "municipal_reservation",
            "service_group": "공공강좌",
            "service_group_policy": "locked",
            "municipality_code": "4121000000",
            "municipality_full_name": "경기도 광명시",
        }
    )
    rows = [
        {
            "provider": target.provider,
            "title": "고정장소 농장 체험",
            "branch": "도시농업과",
            "category": "체험",
            "category_raw": "체험",
            "program_type": "체험",
            "collection_category": "공공예약",
            "domain_category": "체험·견학",
            "source_group": "municipal_reservation",
            "service_group": "체험",
            "service_group_policy": "locked",
            "venue_name": "광명동굴딸기스마트팜",
            "venue_address": "경기 광명시 가학로85번길 142",
        }
    ]

    engine.apply_target_metadata(rows, target)

    assert rows[0]["domain_category"] == "체험·견학"
    assert rows[0]["service_group"] == "체험"
    assert rows[0]["service_group_policy"] == "locked"


def test_main_site_failure_does_not_discard_configured_collection(monkeypatch) -> None:
    municipal = engine.municipal_yaml_module
    target = make_target(url="https://museum.example.test/program/list")
    target.extra.update(
        {
            "discover_from_main_url": True,
            "main_url": "https://museum.example.test/",
        }
    )
    row = {
        "provider": target.provider,
        "title": "Configured course",
        "branch": target.branch,
        "raw_url": target.url,
    }
    monkeypatch.setattr(
        municipal,
        "collect_from_url",
        lambda *_args, **_kwargs: ([row], "configured_parser", {"pages": 1, "detail_pages": 0}),
    )
    monkeypatch.setattr(
        municipal,
        "crawl_experience_main_site",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("bad main certificate")),
    )

    rows, parser, meta = municipal.crawl_experience_from_url(target, timeout=5)

    assert rows == [row]
    assert parser == "configured_parser"
    assert meta["main_discovery_complete"] is False
    assert "RuntimeError" in meta["main_discovery_error"]
    assert meta["source_cap_reached"] is True


def test_generated_yaml_repository_contract_and_every_wrapper_import() -> None:
    report = validate(import_wrappers=True)
    assert report.errors == []
    stats = report.stats
    assert stats["wrappers_imported"] == stats["wrappers"] == stats["registry_providers"]
    assert stats["registry_enabled_providers"] <= stats["registry_providers"] <= stats["source_providers"]
    assert stats["registry_enabled_targets"] <= stats["registry_source_targets"] <= stats["source_targets"]
    assert stats["target_files"] == len(
        [path for path in engine.TARGET_DIR.glob("*.yaml") if path.name != "index.yaml"]
    )


def test_common_collector_transport_is_ssrf_tls_redirect_timeout_and_size_hardened() -> None:
    session = engine.municipal_yaml_module.session()
    try:
        assert isinstance(session, SafeSession)
        assert session.trust_env is False
        assert session.verify is True
        assert 1 <= session.max_redirects <= 10
        assert 1 <= session.total_timeout_seconds <= 300
        assert session.max_response_bytes <= 32 * 1024 * 1024
    finally:
        session.close()


def test_generated_transport_rejects_https_to_http_downgrade() -> None:
    class Response:
        url = "http://example.com/final"
        history: list = []
        closed = False

        def close(self) -> None:
            self.closed = True

    response = Response()
    with pytest.raises(Exception, match="plaintext redirect"):
        engine.reject_tls_downgrade("https://example.com/start", response)
    assert response.closed is True


def test_f508_generated_override_is_atomic_and_complete() -> None:
    assert engine.GENERATED_PROVIDER_ARGUMENT_OVERRIDES[
        "MUNI_WWW_HONGCHEON_GO_KR_F5083BE8"
    ] == (
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "30",
        "--detail-limit",
        "200",
    )


def test_registry_exposes_canonical_structured_arguments_for_orchestration() -> None:
    registry = engine.load_unique_yaml(engine.REGISTRY_FILE)
    rows = {row["provider"]: row for row in registry["targets"]}
    assert rows["MUNI_WWW_DAEDEOK_GO_KR_360B9B7C"]["arguments"] == list(
        engine.GENERATED_PROVIDER_ARGUMENT_OVERRIDES[
            "MUNI_WWW_DAEDEOK_GO_KR_360B9B7C"
        ]
    )
    assert rows["MUNI_LIB_GWE_GO_KR_303FFE72"]["arguments"] == list(
        engine.INSTITUTION_FULL_SNAPSHOT_ARGUMENTS
    )
    assert engine.GENERATED_PROVIDER_ARGUMENT_OVERRIDES[
        "MUNI_YEYAK_DOBONGSISEOL_OR_KR_0A2D506D"
    ] == (
        "--save-db",
        "--per-target-limit",
        "50",
        "--allow-partial-save",
        "--max-pages",
        "120",
        "--detail-limit",
        "1000",
    )
    assert rows["MUNI_GYLIB_JNE_GO_KR_15EB3C2E"]["arguments"] == list(
        engine.INSTITUTION_FULL_SNAPSHOT_ARGUMENTS
    )
    for provider in (
        "NATIONAL_AGRICULTURAL_MUSEUM",
        "NATIONAL_AVIATION_MUSEUM",
    ):
        assert rows[provider]["arguments"] == list(
            engine.INSTITUTION_FULL_SNAPSHOT_ARGUMENTS
        )
    complete_provider_limits = {
        "NATIONAL_MUSEUM_OF_MODERN_ART": ("500", "3000"),
        "SUWON_LIBRARY_MA": ("200", "1000"),
        "SUWON_LIBRARY_SE": ("200", "1000"),
        "ULSAN_EDU_BOOKING": ("100", "3000"),
        "MUNI_LMS_SCHC_GO_KR_A117B76B": ("400", "300"),
        "MUNI_WWW_GOKMG_OR_KR_58036A89": ("60", "100"),
        "MUNI_GSLIB_JNE_GO_KR_80914C01": ("20", "100"),
        "MUNI_GSLIB_JNE_GO_KR_F1BD0233": ("20", "100"),
        "MUNI_EDU_GWANGSAN_GO_KR_C778CD6A": ("20", "500"),
        "BUSAN_DONGGU_RESERVATION": ("300", "200"),
        "MUNI_LLL_BUSAN_GO_KR_944C621B": ("200", "1000"),
        "MUNI_WWW_ULSANNAMGU_GO_KR_A846A0A3": ("30", "20"),
        "MUNI_WWW_PYEONGTAEK_GO_KR_54DAD706": ("30", "250"),
        "MUNI_WWW_PTLIB_GO_KR_D9537B1F": ("50", "100"),
        "MUNI_HONGCHEONLIB_GO_KR_17726A2C": ("20", "200"),
        "MUNI_WWW_HSG_GO_KR_7452F27B": ("20", "100"),
        "MUNI_LIB_HSG_GO_KR_F84FF98D": ("2", "25"),
        "MUNI_LIB_GWE_GO_KR_5CEF7967": ("2", "25"),
        "MUNI_HSYOUTHCENTER_HSG_GO_KR_46DEDE77": ("5", "50"),
        "MUNI_HS_CULTURE_OR_KR_B2E1E14F": ("1", "50"),
        "MUNI_HSG_FAMILYNET_OR_KR_4676E082": ("10", "50"),
        "MUNI_LIB_GWE_GO_KR_20A09F24": ("20", "200"),
        "MUNI_LIB_JEONGSEON_GO_KR_DD359707": ("30", "300"),
        "MUNI_WWW_GEOMDAN_GO_KR_5EA2A3D3": ("300", "400"),
        "MUNI_WWW_OSANEDU_GO_KR_8A50CEDC": ("150", "600"),
        "MUNI_WWW_SEOGU_GWANGJU_KR_10B34AC9": ("100", "100"),
        "MUNI_WWW_GEUMSAN_GO_KR_3E799FCC": ("400", "500"),
        "MUNI_WWW_JINCHEON_GO_KR_081643A9": ("100", "100"),
        "MUNI_WWW_YESAN_GO_KR_AC1B96E1": ("150", "200"),
        "MUNI_TYLIB_GNE_GO_KR_7D159AC1": ("30", "50"),
        "MUNI_WWW_GHLIB_GO_KR_AAEB8BF2": ("100", "200"),
        "MUNI_WWW_JP_GO_KR_44B42971": ("50", "100"),
        "MUNI_WWW_DH_GO_KR_1A4CE8CA": ("120", "300"),
        "SASANG_RESERVATION": ("50", "200"),
    }
    for provider, (max_pages, detail_limit) in complete_provider_limits.items():
        expected_arguments = [
            "--save-db",
            "--mark-stale",
            "--per-target-limit",
            "0",
            "--max-pages",
            max_pages,
            "--detail-limit",
            detail_limit,
        ]
        if provider in engine.MUNICIPAL_OPERATIONAL_PROVIDER_NAMES:
            assert provider not in rows
            assert list(engine.GENERATED_PROVIDER_ARGUMENT_OVERRIDES[provider]) == expected_arguments
        else:
            assert rows[provider]["arguments"] == expected_arguments
    jeju_city_limits = {
        "MUNI_WWW_JEJU_GO_KR_2B65844D": ("30", "100"),
        "MUNI_WWW_JEJUSI_GO_KR_72D06B44": ("40", "100"),
        "MUNI_WWW_JEJUSI_GO_KR_A449522B": ("25", "50"),
        "MUNI_WWW_JEJU_GO_KR_6E577892": ("100", "50"),
        "MUNI_WWW_JEJU_GO_KR_310502FA": ("20", "100"),
        "MUNI_AGRI_JEJU_GO_KR_84F944BE": ("35", "50"),
        "MUNI_WWW_JEJUSI_GO_KR_F9643CD9": ("25", "50"),
        "MUNI_JJDREAMLIB_OR_KR_1A8AAB7D": ("35", "50"),
    }
    for provider, (max_pages, detail_limit) in jeju_city_limits.items():
        assert provider not in rows
        assert engine.GENERATED_PROVIDER_ARGUMENT_OVERRIDES[provider] == (
            "--save-db",
            "--mark-stale",
            "--per-target-limit",
            "0",
            "--max-pages",
            max_pages,
            "--detail-limit",
            detail_limit,
        )
    namwon_arguments = [
        "--save-db",
        "--mark-stale",
        "--per-target-limit",
        "0",
        "--max-pages",
        "100",
        "--detail-limit",
        "2000",
    ]
    if "MUNI_WWW_NAMWON_GO_KR_37D4EA88" in engine.MUNICIPAL_OPERATIONAL_PROVIDER_NAMES:
        assert "MUNI_WWW_NAMWON_GO_KR_37D4EA88" not in rows
        assert list(
            engine.GENERATED_PROVIDER_ARGUMENT_OVERRIDES[
                "MUNI_WWW_NAMWON_GO_KR_37D4EA88"
            ]
        ) == namwon_arguments
    else:
        assert rows["MUNI_WWW_NAMWON_GO_KR_37D4EA88"]["arguments"] == namwon_arguments
    aggregate_only_providers = {
        "MUNI_WWW_SC_GO_KR_84C9C74F",
        "MUNI_SCBAY_SUNCHEON_GO_KR_CC4EA34E",
        "MUNI_EDU_DALSEO_DAEGU_KR_14975995",
        "MUNI_EDU_EUMSEONG_GO_KR_DEC266D9",
        "MUNI_WWW_GBMG_GO_KR_E3F4EA45",
        "MUNI_WWW_SACHEON_GO_KR_2360B3E8",
        "MUNI_WWW_ICBP_GO_KR_61AE4CB0",
        "MUNI_YEYAK_DSSISEOL_OR_KR_8334ABCD",
        "MUNI_YANGGU_GO_KR_19704EDA",
        "MUNI_WWW_ULSANNAMGU_GO_KR_254055C7",
        "MUNI_LIB_GOE_GO_KR_9D32284E",
        "MUNI_WWW_SAHA_GO_KR_ED7CDFC9",
        "MUNI_LLL_BSGANGSEO_GO_KR_0691B6EB",
        "MUNI_WWW_HAEUNDAE_GO_KR_E2AD27FA",
        "SASANG_RESERVATION",
        "MUNI_WWW_YEONJE_GO_KR_73BA35A2",
        "MUNI_WWW_SUYEONG_GO_KR_41E9DDEB",
        "MUNI_EDU_GWA_GO_KR_08B25674",
        "MUNI_NAM_DAEGU_KR_1E00F39A",
        "MUNI_LLL_SUSEONG_KR_2C82AF9F",
        "MUNI_WWW_YEONSU_GO_KR_CB4C41BB",
        "MUNI_GYLLE_GYEYANG_GO_KR_1630ABDE",
        "MUNI_WWW_GIJANG_GO_KR_592C4B5E",
        "MUNI_WWW_GANGHWA_GO_KR_E1374F0C",
        "MUNI_WWW_ONGJIN_GO_KR_0243B215",
        "MUNI_DDC_GSEEK_KR_97F9673C",
        "MUNI_WWW_OSANEDU_GO_KR_8A50CEDC",
        "MUNI_WWW_CHEONGYANG_GO_KR_25520BA7",
        "MUNI_WWW_CNG_GO_KR_84B93860",
        "MUNI_WWW_LIFELONGGEOJE_KR_D866D2AF",
        "MUNI_YEOJU_GSEEK_KR_1034027F",
        "MUNI_WWW_HONGSEONG_GO_KR_C700BF28",
        "MUNI_HONGCHEONLIB_GO_KR_17726A2C",
        "MUNI_EDU_BUYEO_GO_KR_8DF02931",
        "MUNI_WWW_BUAN_GO_KR_B5BDBAE0",
        "MUNI_LLL_ANSAN_GO_KR_691646BE",
        "MUNI_WWW_GC_GO_KR_91618000",
        "MUNI_WWW_FUTURECSY_OR_KR_D9EE9C9C",
        "MUNI_WWW_GYEONGJU_GO_KR_ADA8A467",
        "MUNI_WWW_SJ_WELFARE_OR_KR_335868A2",
        "MUNI_WWW_CHILGOK_GO_KR_B19807DD",
        "MUNI_WWW_HC_GO_KR_3C13AEC0",
        "MUNI_WWW_USC_GO_KR_AFF8D61A",
        "MUNI_WWW_JINAN_GO_KR_3DF1AE69",
        "MUNI_WWW_MUJU_GO_KR_953B498D",
        "MUNI_WWW_JANGSU_GO_KR_2100CCEA",
        "MUNI_WWW_JEONGEUP_GO_KR_C8631DF4",
        "MUNI_WWW_SCEDULIFE_CO_KR_C6522638",
        "MUNI_WWW_GBELIB_KR_04DB1B82",
        "MUNI_WWW_CHEONGDO_GO_KR_0AE7DACF",
        "MUNI_WWW_CHEONGDO_GO_KR_4F44CA8E",
        "MUNI_WWW_CHEONGDO_GO_KR_9BD015B5",
        "MUNI_LIB_IMSIL_GO_KR_C73F4E31",
        "MUNI_WWW_ULJIN_GO_KR_3EFF1FF0",
        "MUNI_WWW_GBELIB_KR_D515FD5D",
        "MUNI_ULLEUNGGUN_FAMILYNET_OR_KR_10E2058E",
        "MUNI_WWW_ULLEUNG_GO_KR_765C23CB",
        "MUNI_WWW_TAEBAEK_GO_KR_89A80ED6",
        "MUNI_WWW_GORYEONG_GO_KR_8F708B74",
        "MUNI_WWW_SANGJU_GO_KR_A813366C",
        "MUNI_WWW_GBGS_GO_KR_87106AA0",
        "MUNI_WWW_GBGS_GO_KR_999BABE7",
        "SEOSAN_WELFARE_TOTAL_RESERVATION",
        "MUNI_WWW_YANGSAN_GO_KR_059D4DD1",
        "MUNI_WWW_YANGSAN_GO_KR_DBBB1885",
        "MUNI_EDUCITY_GEOCHANG_GO_KR_3187BF2A",
        "HAMAN_WELFARE_LIFELONG_COURSE",
        "MUNI_WWW_GIMHAE_GO_KR_48CF9E63",
        "MUNI_WWW_GYERYONG_GO_KR_42F86CD2",
        "MUNI_CN_SEOCHEON_GO_KR_096AAB21",
        "MUNI_WWW_WONJU_GO_KR_56B0C690",
        "MUNI_LIB_GWE_GO_KR_5D9C27C1",
        "MUNI_LLL_PAJU_GO_KR_F639C571",
        "MUNI_PAJU_PCY_OR_KR_412053A6",
        "MUNI_WWW_GCCITY_GO_KR_854A9E81",
        "MUNI_JUMIN_NYJ_GO_KR_4D92ADDF",
        "MUNI_SUGANG_GM_GO_KR_F136DD19",
        "MUNI_WWW_SIHEUNG_GO_KR_0A4570AD",
        "MUNI_SPORTSAPP_SHSI_OR_KR_6239E7D6",
        "MUNI_WWW_HANAM_GO_KR_553EE539",
        "MUNI_WWW_HANAM_GO_KR_04578639",
        "MUNI_ONLINE_HNYOUTH_KR_6F390C33",
        "MUNI_WWW_HDREAM_OR_KR_064EE411",
        "MUNI_WWW_HANAMLIB_GO_KR_EE810F0A",
        "MUNI_WWW_ICHEON_GO_KR_1B4316ED",
        "MUNI_ICHEON_GSEEK_KR_18B68AC1",
        "MUNI_WWW_ICHEONLIB_GO_KR_76E3CE6D",
        "MUNI_WWW_ARTIC_OR_KR_9B6E3C8E",
        "MUNI_CTM_GUNPO_GO_KR_2ADC8672",
        "MUNI_SSO_GUNPO_GO_KR_C6EB5B7F",
        "MUNI_WWW_GUNPOCF_OR_KR_72C2BA1D",
        "MUNI_WWW_GPMEDIA_OR_KR_6517BB69",
        "MUNI_WWW_GUNPOLIB_GO_KR_6657561E",
        "MUNI_WWW_GUNPOUC_OR_KR_C6BD9C41",
        "MUNI_WWW_GPYF_OR_KR_85203167",
        "MUNI_WWW_GUNPO_GO_KR_FE43B335",
        "MUNI_WWW_GUNPOYCF_OR_KR_ED267E43",
        "MUNI_GJEDU_GSEEK_KR_F929637E",
        "MUNI_WWW_GJCITY_GO_KR_CF520672",
        "MUNI_LIB_GJCITY_GO_KR_56EBD1BF",
        "MUNI_WWW_GJCITY_GO_KR_4BA53CE8",
        "MUNI_WWW_GJCITY_GO_KR_5B834C82",
        "MUNI_WWW_GJYOUTH_OR_KR_E2AB883F",
        "MUNI_YPEDU_GSEEK_KR_41263F0B",
        "MUNI_WWW_YP21_GO_KR_EA0D7B81",
        "MUNI_WWW_YP21_GO_KR_632CD45F",
        "MUNI_WWW_YPLIB_GO_KR_C3854B7C",
        "MUNI_RESVE_YONGIN_GO_KR_221336AC",
        "MUNI_JACHI_YONGIN_GO_KR_10340408",
        "MUNI_JACHI_YONGIN_GO_KR_60025DB9",
        "MUNI_JACHI_YONGIN_GO_KR_91C5118C",
        "MUNI_LIB_YONGIN_GO_KR_B7626320",
        "MUNI_WWW_YICF_OR_KR_B2E137D5",
        "MUNI_YIYF_OR_KR_F56DFD54",
        "MUNI_SPORTS_YIYF_OR_KR_206DDBA6",
        "MUNI_WWW_SAMCHEOK_GO_KR_AEA01740",
        "MUNI_YOUTH_SAMCHEOK_GO_KR_96E8E691",
        "MUNI_DGYOUTH_SAMCHEOK_GO_KR_C683FA1B",
        "MUNI_WDYOUTH_SAMCHEOK_GO_KR_AE04F451",
        "MUNI_WWW_HSG_GO_KR_7452F27B",
        "MUNI_LIB_HSG_GO_KR_F84FF98D",
        "MUNI_LIB_GWE_GO_KR_5CEF7967",
        "MUNI_HSYOUTHCENTER_HSG_GO_KR_46DEDE77",
        "MUNI_HS_CULTURE_OR_KR_B2E1E14F",
        "MUNI_HSG_FAMILYNET_OR_KR_4676E082",
        "MUNI_LIB_GWE_GO_KR_20A09F24",
        "MUNI_LIB_JEONGSEON_GO_KR_DD359707",
        "MUNI_WWW_CHEONAN_GO_KR_478DFA4B",
        "MUNI_WWW_CHEONAN_GO_KR_5BC13FB4",
        "MUNI_WWW_CHEONAN_GO_KR_7F8F5560",
        "MUNI_WWW_CHEONAN_GO_KR_C97CA6FD",
        "MUNI_WWW_CHEONAN_GO_KR_EA8D366B",
        "MUNI_WWW_CHEONANLIFEEDU_ORG_41183F3B",
        "MUNI_WWW_XN_2Z1BR4K89DEOA28DJVFZVASSQ98BDZK_KR_81F",
        "MUNI_WWW_JEJU_GO_KR_2B65844D",
        "MUNI_WWW_JEJUSI_GO_KR_72D06B44",
        "MUNI_WWW_JEJUSI_GO_KR_A449522B",
        "MUNI_WWW_JEJU_GO_KR_6E577892",
        "MUNI_WWW_JEJU_GO_KR_310502FA",
        "MUNI_AGRI_JEJU_GO_KR_84F944BE",
        "MUNI_WWW_JEJUSI_GO_KR_F9643CD9",
        "MUNI_JJDREAMLIB_OR_KR_1A8AAB7D",
        "MUNI_YEYAK_DOBONGSISEOL_OR_KR_0A2D506D",
    }
    assert aggregate_only_providers.isdisjoint(rows)
    assert all(row["command"].split()[4:] == row["arguments"] for row in rows.values())
    assert "BABSANG_WELFARE_PROGRAM" not in rows
    assert (engine.ROOT / "Crawler" / "Crawler_BabsangWelfare.py").is_file()


def test_yongin_curl_target_is_an_explicit_duplicate_of_safe_dedicated_scope() -> None:
    document = engine.load_unique_yaml(engine.TARGET_DIR / "lifelong_learning.yaml")
    rows = {row["provider"]: row for row in document["targets"]}
    canonical = rows["YONGIN_LIFELONG_LEARNING"]
    regular = rows["MUNI_LLL_YONGIN_GO_KR_5D4EA93A"]
    root_alias = rows["MUNI_LLL_YONGIN_GO_KR_6CCD566D"]
    assert regular["duplicate_of"] == root_alias["duplicate_of"] == "YONGIN_LIFELONG_LEARNING"
    assert regular["crawler_status"] == root_alias["crawler_status"] == "duplicate_url:YONGIN_LIFELONG_LEARNING"
    assert urlparse(regular["url"]).path == urlparse(canonical["url"]).path
    assert urlparse(root_alias["url"]).hostname == urlparse(canonical["url"]).hostname
    assert canonical["url"] in canonical["list_urls"]
    registry = engine.load_unique_yaml(engine.REGISTRY_FILE)
    assert "MUNI_LLL_YONGIN_GO_KR_5D4EA93A" not in {row["provider"] for row in registry["targets"]}


def test_seongdong_redirect_alias_has_one_direct_registry_owner() -> None:
    document = engine.load_unique_yaml(engine.TARGET_DIR / "lifelong_learning.yaml")
    rows = {row["provider"]: row for row in document["targets"]}
    canonical_provider = "MUNI_DOKSEODANG_SD_GO_KR_A8C20229"
    duplicate_provider = "MUNI_DOKSEODANG_SD_GO_KR_7CF6E0CB"
    canonical = rows[canonical_provider]
    duplicate = rows[duplicate_provider]

    assert "http://dokseodang.sd.go.kr/" in canonical["ownership_aliases"]
    assert duplicate["duplicate_of"] == canonical_provider
    assert duplicate["crawler_status"] == f"duplicate_url:{canonical_provider}"
    assert engine.explicit_duplicate_reason(duplicate) == f"duplicate_of:{canonical_provider}"

    registry = engine.load_unique_yaml(engine.REGISTRY_FILE)
    providers = {row["provider"] for row in registry["targets"]}
    assert canonical_provider in providers
    assert duplicate_provider not in providers
    assert canonical_provider in engine.MUNICIPAL_OPERATIONAL_PROVIDER_NAMES
    assert (engine.ROOT / "Crawler" / "generated_yaml" / f"{canonical_provider}.py").is_file()
    assert not (engine.ROOT / "Crawler" / "generated_yaml" / f"{duplicate_provider}.py").exists()


def test_every_executable_generated_target_uses_https() -> None:
    targets = engine.load_yaml_targets()
    assert targets
    assert all(urlparse(engine.target_url(target)).scheme == "https" for target in targets)
    blocked = {
        row["provider"]: row
        for path in engine.TARGET_DIR.glob("*.yaml")
        if path.name != "index.yaml"
        for row in (engine.load_unique_yaml(path) or {}).get("targets", [])
        if isinstance(row, dict) and row.get("crawler_status") == "blocked"
    }
    assert {
        "MUNI_SJECAMPUS_COM_ECBA8A53",
        "MUNI_WWW_CHEONGJU_GO_KR_6B11C1EE",
        "MUNI_WWW_JPYOUTH_CO_KR_5E838FBF",
    } <= blocked.keys()

    gylib = next(
        target
        for target in targets
        if target["provider"] == "MUNI_GYLIB_JNE_GO_KR_15EB3C2E"
    )
    assert gylib["crawler_status"] == "ready"
    assert urlparse(gylib["url"]).scheme == "https"
    assert not gylib.get("blocked_reason")


def test_no_current_data_targets_require_explicit_recheck_status() -> None:
    provider = "MUNI_WWW_WANDO_GO_KR_AFCA6FD7"

    default_providers = {
        target["provider"] for target in engine.load_yaml_targets()
    }
    recheck_providers = {
        target["provider"]
        for target in engine.load_yaml_targets(extra_statuses={"no_current_data"})
    }

    assert provider not in default_providers
    assert provider in recheck_providers


def test_jne_library_collector_marks_an_empty_followup_page_as_exhausted(monkeypatch) -> None:
    municipal = engine.municipal_yaml_module
    target = make_target(
        provider="MUNI_GYLIB_JNE_GO_KR_15EB3C2E",
        url="https://gylib.jne.go.kr/lecture.es?mid=a50402000000",
    )
    first_page = municipal.BeautifulSoup(
        """
        <html><head><title>수강 신청 : 광양도서관</title></head><body>
          <div class="pagination"></div>
          <table><tr>
            <td>1</td>
            <td><a href="/lecture.es?mid=a50402000000&amp;act=view&amp;el_seq=1">테스트 강좌</a></td>
            <td>성인</td><td>2026-08-01 ~ 2026-08-02 10:00 ~ 11:00</td>
            <td>2026-07-01 10:00 ~ 2026-07-02 17:00</td><td>0 / 10</td><td>접수전</td>
          </tr></table>
        </body></html>
        """,
        "html.parser",
    )
    empty_page = municipal.BeautifulSoup("<html><body><table></table></body></html>", "html.parser")
    monkeypatch.setattr(municipal, "session", lambda: object())
    monkeypatch.setattr(
        municipal,
        "fetch_soup",
        lambda _session, url, **_kwargs: empty_page if "nPage=2" in url else first_page,
    )

    rows, parser, meta = municipal.collect_jne_library_lecture(target, timeout=5, max_pages=5)

    assert parser == "jne_library_lecture_table"
    assert len(rows) == 1
    assert rows[0]["apply_period"] == "2026-07-01 10:00 ~ 2026-07-02 17:00"
    assert municipal.score_fields(rows)["apply_period"] == 1
    assert municipal.sample_rows(rows)[0]["apply_period"] == rows[0]["apply_period"]
    assert meta["pages"] == 2
    assert meta["pagination_detected"] is True
    assert meta["pagination_exhausted"] is True


@pytest.mark.parametrize(
    "arguments",
    [
        ["--all", "--max-pages", str(engine.MAX_PAGES + 1)],
        ["--all", "--detail-limit", str(engine.MAX_DETAIL_PAGES + 1)],
        ["--all", "--timeout", "0"],
        ["--all", "--parallel-workers", "9"],
        ["--all", "--per-target-limit", "5001"],
        ["--all", "--max-depth", "4"],
        ["--all", "--target-limit", "501"],
        ["--all", "--offset", "-1"],
    ],
)
def test_cli_rejects_unbounded_work(arguments: list[str]) -> None:
    with pytest.raises(SystemExit):
        engine.parse_args(arguments)


def test_cli_enforces_persistence_and_stale_boundaries() -> None:
    with pytest.raises(SystemExit):
        engine.parse_args(["--all", "--save-db", "--dry-run"])
    with pytest.raises(SystemExit):
        engine.parse_args(["--all", "--mark-stale"])
    with pytest.raises(SystemExit):
        engine.parse_args(["--all", "--save-db", "--mark-stale"])
    with pytest.raises(SystemExit):
        engine.parse_args(["--all", "--save-db", "--include-status", "needs_parser"])
    with pytest.raises(SystemExit):
        engine.parse_args(["--all", "--save-db", "--per-target-limit", "50"])
    with pytest.raises(SystemExit):
        engine.parse_args(["--all", "--save-db", "--per-target-limit", "0", "--allow-partial-save"])
    sampled = engine.parse_args(
        ["--all", "--save-db", "--per-target-limit", "50", "--allow-partial-save"]
    )
    assert sampled.save_db is True and sampled.allow_partial_save is True
    args = engine.parse_args(["--all", "--save-db", "--mark-stale", "--per-target-limit", "0"])
    assert args.save_db is True
    assert args.mark_stale is True
    recheck = engine.parse_args(
        [
            "--all",
            "--save-db",
            "--mark-stale",
            "--per-target-limit",
            "0",
            "--include-status",
            "no_current_data",
        ]
    )
    assert recheck.include_status == ["no_current_data"]


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "file:///etc/passwd",
        "https://user:password@example.com/path",
        "https://example.com/path\nforged",
        "https://example.com/path?access_token=secret",
    ],
)
def test_target_url_validation_rejects_unsafe_or_secret_urls(url: str) -> None:
    if "access_token" in url:
        row = {
            "provider": "TEST",
            "name": "name",
            "branch": "branch",
            "collection_category": "category",
            "domain_category": "category",
            "operator_type": "operator",
            "source_group": "source_group",
            "collection_type": "static_html",
            "crawler_status": "ready",
            "priority": 1,
            "url": url,
            "source": "source",
            "origin": "test",
        }
        with pytest.raises(ValueError):
            engine.validate_target_row(row)
    else:
        with pytest.raises(ValueError):
            engine.normalize_http_url(url, required=True)


@pytest.mark.parametrize("field,value", [("item_selector", "div["), ("items_path", "$.items[?(@.x)]")])
def test_optional_selector_and_json_path_syntax_fail_closed(field: str, value: str) -> None:
    row = {
        "provider": "TEST",
        "name": "name",
        "branch": "branch",
        "collection_category": "category",
        "domain_category": "category",
        "operator_type": "operator",
        "source_group": "source_group",
        "collection_type": "static_html",
        "crawler_status": "ready",
        "priority": 1,
        "url": "https://example.com/courses",
        "source": "source",
        "origin": "test",
        field: value,
    }
    with pytest.raises(ValueError):
        engine.validate_target_row(row)


def test_normalize_collected_rows_filters_required_fields_urls_dates_and_identity_collisions() -> None:
    target = make_target()
    rows = engine.normalize_collected_rows(
        [
            {
                "title": "Course A",
                "raw_url": "https://example.com/detail",
                "application_url": "javascript:alert(1)",
                "period": "2026.07.01 ~ 2026.08.01",
            },
            {
                "title": "Course B",
                "raw_url": "https://example.com/detail",
                "period": "2026.07.02 ~ 2026.08.02",
            },
            {"title": "", "raw_url": "https://example.com/empty"},
            {
                "title": "Reverse",
                "raw_url": "https://example.com/reverse",
                "period": "2026.08.02 ~ 2026.07.01",
            },
        ],
        target,
    )
    assert [row["title"] for row in rows] == ["Course A", "Course B"]
    assert "application_url" not in rows[0]
    assert rows[0]["provider_course_id"] != rows[1]["provider_course_id"]
    assert len({row["raw_url"] for row in rows}) == 2
    assert all(
        row["raw_url"].startswith("https://example.com/detail#mooncen-item-")
        for row in rows
    )


def test_per_target_failures_are_isolated_and_explicit_dry_run_never_opens_db(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeWriter:
        def __init__(self, provider: str):
            self.provider = provider

        def normalize_branch_split_row(self, row: dict) -> None:
            row.setdefault("branch_code", self.provider)

    def fake_collect(target: CrawlTarget, **_: object):
        if target.provider == "FAIL_PROVIDER":
            raise RuntimeError("token=super-secret")
        return (
            [{"title": "Valid", "raw_url": target.url}],
            "fixture",
            {"pages": 1, "detail_pages": 0, "recursion_depth": 0},
        )

    monkeypatch.setattr(engine, "MunicipalDbWriter", FakeWriter)
    monkeypatch.setattr(engine, "collect_from_url", fake_collect)
    monkeypatch.setattr(engine, "get_db_connection", lambda: pytest.fail("dry-run opened the database"))
    reports = engine.run_targets(
        [make_target("OK_PROVIDER"), make_target("FAIL_PROVIDER")],
        per_target_limit=1,
        save_db=False,
        mark_stale=False,
        max_depth=1,
        max_pages=1,
        detail_limit=1,
        timeout=5,
        parallel_workers=2,
    )
    assert [report.success for report in reports] == [True, False]
    assert "super-secret" not in reports[1].error
    assert "[REDACTED]" in reports[1].error


def test_transient_zero_row_retry_only_recollects_failed_transport_child_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeWriter:
        def __init__(self, provider: str):
            self.provider = provider

        def normalize_branch_split_row(self, row: dict) -> None:
            row.setdefault("branch_code", self.provider)

    calls = {"HEALTHY": 0, "FLAKY": 0, "CONTRACT": 0}
    sleeps: list[float] = []

    def fake_collect(target: CrawlTarget, **_: object):
        calls[target.provider] += 1
        if target.provider == "FLAKY" and calls[target.provider] == 1:
            return (
                [],
                "fixture",
                {
                    "pages": 0,
                    "configured_collection_error": (
                        "first page fetch RequestException: HTTPSConnectionPool "
                        "ConnectTimeout"
                    ),
                },
            )
        if target.provider == "CONTRACT":
            return (
                [],
                "fixture",
                {
                    "pages": 1,
                    "configured_collection_error": (
                        "GyeyangContractError: citizen page 1 changed during stable "
                        "recheck after RequestException recovery"
                    ),
                },
            )
        return (
            [{"title": f"{target.provider} course", "raw_url": target.url}],
            "fixture",
            {
                "pages": 1,
                "pagination_complete": True,
                "snapshot_complete": True,
            },
        )

    monkeypatch.setattr(engine, "MunicipalDbWriter", FakeWriter)
    monkeypatch.setattr(engine, "collect_from_url", fake_collect)
    monkeypatch.setattr(engine.time, "sleep", sleeps.append)
    monkeypatch.setattr(
        engine,
        "get_db_connection",
        lambda: pytest.fail("dry-run opened the database"),
    )

    reports = engine.run_targets(
        [
            make_target("HEALTHY"),
            make_target("FLAKY"),
            make_target("CONTRACT"),
        ],
        per_target_limit=0,
        save_db=False,
        mark_stale=False,
        max_depth=1,
        max_pages=2,
        detail_limit=1,
        timeout=5,
        parallel_workers=2,
    )

    assert calls == {"HEALTHY": 1, "FLAKY": 2, "CONTRACT": 1}
    assert sleeps == [engine.TRANSIENT_ZERO_ROW_RETRY_BACKOFF_SECONDS]
    assert [report.success for report in reports] == [True, True, False]
    assert "stable recheck" in reports[2].error


@pytest.mark.parametrize(
    ("error", "retryable"),
    [
        (
            "detail I100000/224/16425: HTTPSConnectionPool: "
            "Max retries exceeded with url caused by ConnectTimeoutError",
            True,
        ),
        ("RequestException: Strict TLS request failed", True),
        ("Yangju education first-page fetch failed: RequestException", True),
        ("garden list/sentinel fetch RequestException", True),
        ("SSLError: UNEXPECTED_EOF_WHILE_READING", True),
        ("HTTPSConnectionPool: Read timed out (read timeout=20.0)", True),
        ("lifelong page 3: stable boundary recheck changed", False),
        (
            "GyeyangContractError: citizen page 1 changed during stable recheck",
            False,
        ),
        ("Parser error: declared total does not match parsed rows", False),
    ],
)
def test_zero_row_retry_classifier_matches_observed_transport_not_contract_errors(
    error: str,
    retryable: bool,
) -> None:
    target = make_target("FAILED_CHILD")
    result = engine._CollectionResult(
        target=target,
        report=ProviderReport(
            provider=target.provider,
            name=target.name,
            url=target.url,
            configured_collection_error=error,
            error=error,
        ),
        rows=[],
        stale_cutoff=engine.utc_now(),
    )

    assert bool(engine._retryable_zero_row_transport_marker(result)) is retryable


def test_collector_cap_violation_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeWriter:
        def __init__(self, provider: str):
            self.provider = provider

        def normalize_branch_split_row(self, row: dict) -> None:
            return None

    monkeypatch.setattr(engine, "MunicipalDbWriter", FakeWriter)
    monkeypatch.setattr(
        engine,
        "collect_from_url",
        lambda target, **kwargs: (
            [{"title": "Unsafe over-fetch", "raw_url": target.url}],
            "fixture",
            {"pages": 1, "detail_pages": 2, "recursion_depth": 0},
        ),
    )
    result = engine._collect_single_target(make_target(), 1, 1, 1, 1, 5)
    assert result.rows == []
    assert result.report.success is False
    assert "exceeded detail_limit" in result.report.error


def test_per_target_sample_limit_is_reported_as_incomplete(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeWriter:
        def __init__(self, provider: str):
            self.provider = provider

        def normalize_branch_split_row(self, row: dict) -> None:
            return None

    monkeypatch.setattr(engine, "MunicipalDbWriter", FakeWriter)
    monkeypatch.setattr(
        engine,
        "collect_from_url",
        lambda target, **_kwargs: (
            [
                {"title": "First", "raw_url": f"{target.url}/1"},
                {"title": "Second", "raw_url": f"{target.url}/2"},
            ],
            "fixture",
            {"pages": 1, "detail_pages": 0, "recursion_depth": 0},
        ),
    )

    result = engine._collect_single_target(make_target(), 1, 1, 2, 1, 5)

    assert [row["title"] for row in result.rows] == ["First"]
    assert result.row_cap_reached is True
    assert result.collection_complete is False


def test_full_snapshot_validation_satisfies_pagination_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeWriter:
        def __init__(self, provider: str):
            self.provider = provider

        def normalize_branch_split_row(self, row: dict) -> None:
            return None

    monkeypatch.setattr(engine, "MunicipalDbWriter", FakeWriter)
    monkeypatch.setattr(
        engine,
        "collect_from_url",
        lambda target, **_kwargs: (
            [{"title": "Complete", "raw_url": target.url}],
            "fixture",
            {
                "pages": 2,
                "detail_pages": 1,
                "pagination_detected": True,
                "snapshot_complete": True,
                "full_snapshot_validated": True,
            },
        ),
    )

    result = engine._collect_single_target(make_target(), 0, 1, 3, 2, 5)

    assert result.report.success is True
    assert result.collection_complete is True
    assert result.page_cap_reached is False
    assert result.detail_cap_reached is False


def test_configured_collection_error_blocks_complete_state_and_stale_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeWriter:
        def __init__(self, provider: str):
            self.provider = provider

        def normalize_branch_split_row(self, row: dict) -> None:
            return None

    monkeypatch.setattr(engine, "MunicipalDbWriter", FakeWriter)
    monkeypatch.setattr(
        engine,
        "collect_from_url",
        lambda target, **_kwargs: (
            [{"title": "Partial row", "raw_url": target.url}],
            "fixture",
            {
                "pages": 1,
                "detail_pages": 0,
                "pagination_complete": True,
                "configured_collection_error": "one configured source failed",
            },
        ),
    )

    result = engine._collect_single_target(make_target(), 0, 1, 2, 1, 5)

    assert result.report.configured_collection_error == "one configured source failed"
    assert result.report.error == "one configured source failed"
    assert result.collection_complete is False


def test_each_generated_target_runs_inside_one_global_request_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeWriter:
        def __init__(self, provider: str):
            self.provider = provider

        def normalize_branch_split_row(self, row: dict) -> None:
            return None

    budgets = []

    @engine.contextmanager
    def fake_budget(maximum_requests: int):
        budgets.append(maximum_requests)
        yield

    monkeypatch.setattr(engine, "MunicipalDbWriter", FakeWriter)
    monkeypatch.setattr(engine, "outbound_request_budget", fake_budget)
    monkeypatch.setattr(
        engine,
        "collect_from_url",
        lambda target, **kwargs: (
            [{"title": "Bounded", "raw_url": target.url}],
            "fixture",
            {"pages": 1, "detail_pages": 0, "recursion_depth": 0},
        ),
    )

    result = engine._collect_single_target(make_target(), 1, 2, 3, 4, 5)

    assert result.report.success is True
    assert budgets == [54]


class FakeConnection:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0
        self.closed = 0

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closed += 1


def collection_result(
    provider: str,
    title: str = "Course",
    url: str = "https://example.com/courses",
) -> engine._CollectionResult:
    target = make_target(provider, url)
    report = ProviderReport(provider=provider, name=provider, url=target.url, success=True, collected=1, pages=1)
    return engine._CollectionResult(
        target=target,
        report=report,
        rows=[{"title": title, "raw_url": target.url}],
        stale_cutoff=engine.utc_now(),
        collection_complete=True,
    )


def test_provider_rows_and_stale_update_commit_in_one_transaction(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = FakeConnection()
    stale_calls: list[tuple[str, str]] = []

    class FakeWriter:
        def __init__(self, provider: str):
            self.provider = provider

        def save_rows(self, rows: list[dict]) -> int:
            return len(rows)

    monkeypatch.setattr(engine, "get_db_connection", lambda: connection)
    monkeypatch.setattr(engine, "MunicipalDbWriter", FakeWriter)
    monkeypatch.setattr(
        engine,
        "mark_stale_courses",
        lambda provider, cutoff, *, source_endpoint: stale_calls.append(
            (provider, source_endpoint)
        ),
    )
    results = [
        collection_result("ATOMIC_PROVIDER", "A", "https://example.test/a"),
        collection_result("ATOMIC_PROVIDER", "B", "https://example.test/b"),
    ]
    engine._persist_collection_results(
        results,
        mark_stale=True,
        max_pages=20,
        per_target_limit=0,
        complete_providers={"ATOMIC_PROVIDER"},
    )
    assert connection.commits == 1
    assert connection.rollbacks == 0
    assert connection.closed == 1
    assert stale_calls == [
        ("ATOMIC_PROVIDER", "https://example.test/a"),
        ("ATOMIC_PROVIDER", "https://example.test/b"),
    ]
    assert [result.report.saved for result in results] == [1, 1]
    assert all(result.persistence_succeeded for result in results)


def test_provider_transaction_rolls_back_all_reports_on_any_save_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = FakeConnection()

    class FailingWriter:
        def __init__(self, provider: str):
            self.calls = 0

        def save_rows(self, rows: list[dict]) -> int:
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("write failed")
            return len(rows)

    monkeypatch.setattr(engine, "get_db_connection", lambda: connection)
    monkeypatch.setattr(engine, "MunicipalDbWriter", FailingWriter)
    results = [collection_result("ROLLBACK_PROVIDER", "A"), collection_result("ROLLBACK_PROVIDER", "B")]
    engine._persist_collection_results(
        results,
        mark_stale=False,
        max_pages=20,
        per_target_limit=0,
        complete_providers={"ROLLBACK_PROVIDER"},
    )
    assert connection.commits == 0
    assert connection.rollbacks == 1
    assert all(result.report.saved == 0 and not result.report.success for result in results)
    assert all(not result.persistence_succeeded for result in results)


def test_failed_sibling_blocks_every_provider_database_write(monkeypatch: pytest.MonkeyPatch) -> None:
    successful = collection_result("SIBLING_PROVIDER", "A")
    failed = collection_result("SIBLING_PROVIDER", "B")
    failed.report.success = False
    failed.report.error = "target failed"
    monkeypatch.setattr(engine, "get_db_connection", lambda: pytest.fail("failed provider opened the database"))

    engine._persist_collection_results(
        [successful, failed],
        mark_stale=False,
        max_pages=20,
        per_target_limit=50,
        complete_providers=set(),
        allow_partial_save=True,
    )

    assert successful.report.saved == 0
    assert failed.report.saved == 0
    assert successful.persistence_succeeded is False
    assert failed.persistence_succeeded is False


def test_concrete_provider_manifest_reports_only_committed_provider_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    committed = collection_result("COMMITTED_PROVIDER", "A")
    committed.persistence_succeeded = True
    skipped = collection_result("SKIPPED_PROVIDER", "B")
    skipped.report.success = False
    skipped.report.saved = 0
    manifest_path = tmp_path / "concrete-results.json"
    monkeypatch.setenv(engine.CONCRETE_RESULT_MANIFEST_PATH_ENV, str(manifest_path))
    monkeypatch.setenv(engine.SCHEDULED_PROVIDER_ENV, "EXPERIENCE_TARGETS")
    monkeypatch.setenv("CRAWL_BATCH_ID", "test-batch")

    written_path = engine._write_concrete_provider_result_manifest(
        [committed, skipped],
        save_db=True,
    )

    assert written_path == manifest_path
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["scheduled_provider"] == "EXPERIENCE_TARGETS"
    assert manifest["crawl_batch_id"] == "test-batch"
    assert manifest["providers"] == [
        {
            "provider": "COMMITTED_PROVIDER",
            "success": True,
            "targets_total": 1,
            "targets_succeeded": 1,
            "collected_courses": 1,
            "saved_courses": 0,
        },
        {
            "provider": "SKIPPED_PROVIDER",
            "success": False,
            "targets_total": 1,
            "targets_succeeded": 0,
            "collected_courses": 1,
            "saved_courses": 0,
        },
    ]


def test_incomplete_rows_require_explicit_bounded_partial_save(monkeypatch: pytest.MonkeyPatch) -> None:
    result = collection_result("IMPLICIT_PARTIAL")
    result.collection_complete = False
    result.page_cap_reached = True
    monkeypatch.setattr(engine, "get_db_connection", lambda: pytest.fail("implicit partial opened the database"))

    engine._persist_collection_results(
        [result],
        mark_stale=False,
        max_pages=20,
        per_target_limit=50,
        complete_providers=set(),
    )

    assert result.report.saved == 0
    assert result.report.success is False
    assert "Incomplete collection" in result.report.error


def test_explicit_bounded_partial_save_preserves_existing_sample_behavior(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = FakeConnection()

    class FakeWriter:
        def __init__(self, provider: str):
            self.provider = provider

        def save_rows(self, rows: list[dict]) -> int:
            return len(rows)

    result = collection_result("EXPLICIT_PARTIAL")
    result.collection_complete = False
    result.page_cap_reached = True
    monkeypatch.setattr(engine, "get_db_connection", lambda: connection)
    monkeypatch.setattr(engine, "MunicipalDbWriter", FakeWriter)

    engine._persist_collection_results(
        [result],
        mark_stale=False,
        max_pages=20,
        per_target_limit=50,
        complete_providers=set(),
        allow_partial_save=True,
    )

    assert connection.commits == 1
    assert connection.rollbacks == 0
    assert result.report.success is True
    assert result.report.saved == 1


def test_stale_is_suppressed_without_full_uncapped_provider_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = FakeConnection()
    stale_calls: list[str] = []

    class FakeWriter:
        def __init__(self, provider: str):
            pass

        def save_rows(self, rows: list[dict]) -> int:
            return len(rows)

    monkeypatch.setattr(engine, "get_db_connection", lambda: connection)
    monkeypatch.setattr(engine, "MunicipalDbWriter", FakeWriter)
    monkeypatch.setattr(engine, "mark_stale_courses", lambda provider, cutoff: stale_calls.append(provider))
    result = collection_result("INCOMPLETE_PROVIDER")
    result.row_cap_reached = True
    engine._persist_collection_results(
        [result],
        mark_stale=True,
        max_pages=20,
        per_target_limit=0,
        complete_providers={"INCOMPLETE_PROVIDER"},
    )
    assert stale_calls == []


@pytest.mark.parametrize(
    "flag",
    ["page_cap_reached", "detail_cap_reached", "recursion_cap_reached"],
)
def test_stale_is_suppressed_when_any_collection_cap_is_reached(
    monkeypatch: pytest.MonkeyPatch,
    flag: str,
) -> None:
    connection = FakeConnection()
    stale_calls: list[str] = []

    class FakeWriter:
        def __init__(self, provider: str):
            pass

        def save_rows(self, rows: list[dict]) -> int:
            return len(rows)

    monkeypatch.setattr(engine, "get_db_connection", lambda: connection)
    monkeypatch.setattr(engine, "MunicipalDbWriter", FakeWriter)
    monkeypatch.setattr(engine, "mark_stale_courses", lambda provider, cutoff: stale_calls.append(provider))
    result = collection_result("CAPPED_PROVIDER")
    setattr(result, flag, True)
    engine._persist_collection_results(
        [result],
        mark_stale=True,
        max_pages=20,
        per_target_limit=0,
        complete_providers={"CAPPED_PROVIDER"},
    )
    assert stale_calls == []


def test_stale_is_suppressed_without_explicit_collection_completeness(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = FakeConnection()
    stale_calls: list[str] = []

    class FakeWriter:
        def __init__(self, provider: str):
            pass

        def save_rows(self, rows: list[dict]) -> int:
            return len(rows)

    monkeypatch.setattr(engine, "get_db_connection", lambda: connection)
    monkeypatch.setattr(engine, "MunicipalDbWriter", FakeWriter)
    monkeypatch.setattr(engine, "mark_stale_courses", lambda provider, cutoff: stale_calls.append(provider))
    result = collection_result("INCOMPLETE_PROVIDER")
    result.collection_complete = False
    engine._persist_collection_results(
        [result],
        mark_stale=True,
        max_pages=20,
        per_target_limit=0,
        complete_providers={"INCOMPLETE_PROVIDER"},
    )
    assert stale_calls == []


def test_partial_failure_is_not_hidden_by_main_exit_code(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    target = make_target("EXIT_PROVIDER")
    monkeypatch.setattr(engine, "load_yaml_targets", lambda **kwargs: [target.extra])
    monkeypatch.setattr(engine, "select_targets", lambda *args, **kwargs: [target])
    monkeypatch.setattr(
        engine,
        "run_targets",
        lambda *args, **kwargs: [
            ProviderReport(provider="A", name="A", url="https://example.com", success=True),
            ProviderReport(provider="B", name="B", url="https://example.com", success=False, error="failed"),
        ],
    )
    monkeypatch.setattr(engine, "print_table", lambda reports: None)
    monkeypatch.setattr(engine, "write_report", lambda reports: tmp_path / "report.yaml")
    assert engine.main(["--provider", "EXIT_PROVIDER", "--dry-run"]) == 1


def test_duplicate_yaml_keys_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.yaml"
    path.write_text("version: 1\nversion: 2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate YAML mapping key"):
        engine.load_unique_yaml(path)


def test_wrapper_generation_fails_before_writing_on_provider_collision(tmp_path: Path) -> None:
    output = tmp_path / "generated"
    with pytest.raises(ValueError, match="Duplicate provider"):
        write_wrappers([{"provider": "SAME"}, {"provider": "SAME"}], output_dir=output)
    assert list(output.glob("*.py")) == []


def test_wrapper_manifest_cleanup_preserves_infrastructure_modules(tmp_path: Path) -> None:
    output = tmp_path / "generated"
    output.mkdir()
    infrastructure = output / "helper.py"
    infrastructure.write_text("VALUE = 1\n", encoding="utf-8")
    write_wrappers([{"provider": "ONE"}], output_dir=output)
    assert infrastructure.read_text(encoding="utf-8") == "VALUE = 1\n"
    assert (output / "ONE.py").is_file()
    assert (output / ".generated_wrappers.yaml").is_file()
    write_wrappers([], output_dir=output)
    assert infrastructure.is_file()
    assert not (output / "ONE.py").exists()


def test_wrapper_manifest_refuses_to_delete_modified_managed_file(tmp_path: Path) -> None:
    output = tmp_path / "generated"
    write_wrappers([{"provider": "ONE"}], output_dir=output)
    wrapper = output / "ONE.py"
    wrapper.write_text(wrapper.read_text(encoding="utf-8") + "# local edit\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="Refusing to delete modified"):
        write_wrappers([], output_dir=output)
    assert wrapper.is_file()
