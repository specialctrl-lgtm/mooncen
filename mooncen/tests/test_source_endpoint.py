from utils.source_endpoint import canonical_source_endpoint


def test_staging_close_is_scoped_when_provider_rows_have_entry_points(monkeypatch) -> None:
    import tools.apply_staging_batch as staging

    executed: list[tuple[str, object]] = []
    bulk: list[tuple[str, list[tuple[str, str]]]] = []

    class Cursor:
        rowcount = 3

        def execute(self, statement, params=None):
            executed.append((" ".join(str(statement).split()), params))

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    class Connection:
        def cursor(self):
            return Cursor()

    monkeypatch.setattr(
        staging,
        "execute_values",
        lambda _cursor, statement, values: bulk.append((statement, list(values))),
    )

    closed = staging.close_missing_courses(
        Connection(),
        "batch-id",
        ["MULTI", "LEGACY"],
        [
            {
                "provider": "MULTI",
                "provider_course_id": "a-1",
                "source_endpoint": "https://example.go.kr/course/a",
            },
            {
                "provider": "LEGACY",
                "provider_course_id": "legacy-1",
                "source_endpoint": None,
            },
        ],
    )

    assert closed == 3
    update_sql, params = executed[-1]
    assert "scope.source_endpoint = c.source_endpoint" in update_sql
    assert params == (["MULTI", "LEGACY"], ["LEGACY"])
    assert bulk[-1][1] == [("MULTI", "https://example.go.kr/course/a")]


def test_source_endpoint_is_stable_across_pagination_and_query_order() -> None:
    first = canonical_source_endpoint(
        "HTTPS://Example.GO.KR:443/course/list.do?pageIndex=1&category=adult&status=open#top"
    )
    next_page = canonical_source_endpoint(
        "https://example.go.kr/course/list.do?status=open&pageIndex=9&category=adult"
    )

    assert first == "https://example.go.kr/course/list.do?category=adult&status=open"
    assert next_page == first


def test_source_endpoint_rejects_credentials_and_non_http_schemes() -> None:
    assert canonical_source_endpoint("https://user:pass@example.go.kr/course") == ""
    assert canonical_source_endpoint("javascript:alert(1)") == ""
