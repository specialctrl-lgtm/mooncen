from __future__ import annotations

import sys
from pathlib import Path


PROVIDER = "MUNI_EDU_DOBONG_GO_KR_5ADA6E67"
PROVIDER_NAME = "도봉구 교육포털 정보화교육"
LIST_PATH = "/Course_Lifelong/lecture_G_Lst.asp"
DETAIL_PATH = "/Course_Lifelong/lecture_Vw.asp"
COURSE_CODE = "10007722"
COURSE_GUBUN = "G"
COURSE_GNB = "GnbTp7"


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Crawler.generated_yaml import MUNI_EDU_DOBONG_GO_KR_905EFB5D as dobong  # noqa: E402


def configure() -> None:
    dobong.PROVIDER = PROVIDER
    dobong.PROVIDER_NAME = PROVIDER_NAME
    dobong.LIST_PATH = LIST_PATH
    dobong.DETAIL_PATH = DETAIL_PATH
    dobong.COURSE_CODE = COURSE_CODE
    dobong.COURSE_GUBUN = COURSE_GUBUN
    dobong.COURSE_GNB = COURSE_GNB
    dobong.BRANCH_NAME = "도봉구 정보화교육"
    dobong.BRANCH_ADDRESS = "서울특별시 도봉구 마들로 656"


def main() -> int:
    configure()
    return dobong.main()


if __name__ == "__main__":
    raise SystemExit(main())
