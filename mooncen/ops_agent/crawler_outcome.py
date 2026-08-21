from __future__ import annotations


CRAWLER_SUCCESS_EXIT_CODE = 0
CRAWLER_FAILED_EXIT_CODE = 1
# Keep this non-zero so shell/systemd callers can distinguish an incomplete
# crawl from a complete one while explicitly reviewed consumers may accept it.
CRAWLER_PARTIAL_SUCCESS_EXIT_CODE = 3


def ops_status_for_crawler_exit_code(return_code: int | None) -> str:
    """Translate the reviewed crawler process contract into an Ops status."""

    if return_code == CRAWLER_SUCCESS_EXIT_CODE:
        return "success"
    if return_code == CRAWLER_PARTIAL_SUCCESS_EXIT_CODE:
        return "partial_success"
    return "failed"


__all__ = [
    "CRAWLER_FAILED_EXIT_CODE",
    "CRAWLER_PARTIAL_SUCCESS_EXIT_CODE",
    "CRAWLER_SUCCESS_EXIT_CODE",
    "ops_status_for_crawler_exit_code",
]
