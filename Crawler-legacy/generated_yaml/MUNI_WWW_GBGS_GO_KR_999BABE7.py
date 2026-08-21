from __future__ import annotations

import sys
from pathlib import Path


PROVIDER = "MUNI_WWW_GBGS_GO_KR_999BABE7"
PROVIDER_NAME = "경산시 평생학습관 평생학습 프로그램"


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Crawler.generated_yaml import MUNI_WWW_GBGS_GO_KR_4D7732DD as gbgs  # noqa: E402


def configure() -> None:
    gbgs.PROVIDER = PROVIDER
    gbgs.PROVIDER_NAME = PROVIDER_NAME
    gbgs.LIST_PATH = "/lll/page/2400/1604.tc"
    gbgs.MN = "2400"
    gbgs.PAGE_NO = "1604"
    gbgs.SEARCH_INST_NO = "2"
    gbgs.CATEGORY_RAW = "평생학습 프로그램"
    gbgs.DEFAULT_BRANCH = "경산시 평생학습관"
    gbgs.DEFAULT_ADDRESS = "경상북도 경산시"
    gbgs.DEFAULT_TARGET = "경산시민"
    gbgs.LIST_URL = (
        f"{gbgs.BASE_URL}{gbgs.LIST_PATH}?mn={gbgs.MN}&pageIndex=1&pageNo={gbgs.PAGE_NO}"
        f"&paramIdx=&eduNo=-1&searchInstNo={gbgs.SEARCH_INST_NO}"
        "&srchCtgryCd=&srchLlPrgrmCd=&srchRgnCd=&srchEduNm="
    )


def main() -> int:
    configure()
    return gbgs.main()


if __name__ == "__main__":
    raise SystemExit(main())
