from __future__ import annotations

import sys
from pathlib import Path


PROVIDER = "MUNI_LLL_SUSEONG_KR_F59F7BFE"
PROVIDER_NAME = "수성구 평생교육 플랫폼 러닝톡 강좌 및 수강신청"
LIST_URL = "https://lll.suseong.kr/index.do?menu_id=00001969"


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Crawler.generated_yaml import MUNI_LLL_SUSEONG_KR_2C82AF9F as base  # noqa: E402


def configure_provider() -> None:
    base.PROVIDER = PROVIDER
    base.PROVIDER_NAME = PROVIDER_NAME
    base.LIST_URL = LIST_URL
    base.logger = base.setup_logger("Crawler_SuseongLearningTalkMenu")


def main() -> int:
    configure_provider()
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
