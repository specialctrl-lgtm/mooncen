from tools.generate_frontend_sitemap import build_sitemap, is_indexable_category, normalize_site_url


def test_sitemap_category_quality_rejects_thin_or_internal_taxonomy_values() -> None:
    assert is_indexable_category({"category": "미술·공예", "course_count": 12}) is True
    assert is_indexable_category({"category": "미술·공예", "course_count": 2}) is False
    assert is_indexable_category({"category": "검토필요", "course_count": 100}) is False
    assert is_indexable_category({"category": "기타", "course_count": 100}) is False
    assert is_indexable_category({"category": "기자 2025-01-20", "course_count": 100}) is False


def test_build_sitemap_omits_non_indexable_categories() -> None:
    xml = build_sitemap(
        "https://mooncen.kr",
        courses=[],
        categories=[
            {"category": "미술·공예", "course_count": 4, "updated_at": "2026-07-10"},
            {"category": "검토필요", "course_count": 99, "updated_at": "2026-07-10"},
        ],
        branches=[],
    )

    assert "%EB%AF%B8%EC%88%A0-%EA%B3%B5%EC%98%88" in xml
    assert "%EA%B2%80%ED%86%A0%ED%95%84%EC%9A%94" not in xml


def test_site_url_must_be_a_clean_http_origin() -> None:
    assert normalize_site_url("mooncen.kr/") == "https://mooncen.kr"
    assert normalize_site_url("http://localhost:5173/") == "http://localhost:5173"

    for value in (
        "ftp://example.com",
        "https://user:pass@example.com",
        "https://example.com/base",
        "https://example.com/?token=x",
    ):
        try:
            normalize_site_url(value)
        except ValueError:
            pass
        else:
            raise AssertionError(f"unsafe site URL accepted: {value}")
