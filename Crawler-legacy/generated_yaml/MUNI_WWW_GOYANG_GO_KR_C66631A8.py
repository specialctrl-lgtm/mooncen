from __future__ import annotations

import sys
from pathlib import Path


PROVIDER = "MUNI_WWW_GOYANG_GO_KR_C66631A8"
PROVIDER_NAME = "고양시 통합예약 교육강좌 일산동구"
GU_CODE = "396010000"


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Crawler.generated_yaml import MUNI_WWW_GOYANG_GO_KR_9C1A7354 as goyang  # noqa: E402


def configure() -> None:
    goyang.PROVIDER = PROVIDER
    goyang.PROVIDER_NAME = PROVIDER_NAME
    goyang.DEFAULT_BRANCH = "고양시 통합예약 일산동구"
    goyang.GU_CODES = [GU_CODE]


def main() -> int:
    configure()
    return goyang.main()


if __name__ == "__main__":
    raise SystemExit(main())
