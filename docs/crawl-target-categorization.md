# MoonCen 수집 카테고리 분리

## 목적

수집 대상이 늘어나면서 사이트 목록, 사용자 카테고리, 크롤러 방식, 운영 상태가 한 파일에 섞이지 않도록 분리한다.

## 카테고리 파일

분리된 YAML은 `config/crawl_targets/` 아래에 저장한다.

| 파일 | 용도 |
| --- | --- |
| `retail_culture.yaml` | 대형마트/백화점 문화센터 |
| `lifelong_learning.yaml` | 지자체 평생학습/교육 |
| `public_reservation.yaml` | 공공예약/통합예약 |
| `library.yaml` | 도서관 강좌 |
| `sports_facility.yaml` | 체육/스포츠 시설 |
| `welfare.yaml` | 복지관 |
| `youth.yaml` | 청소년시설 |
| `museum_science.yaml` | 박물관/과학관 |
| `arboretum_ecology.yaml` | 수목원/생태/생물자원관 |
| `arts_culture.yaml` | 예술/공연/문화재단 |
| `generated_review.yaml` | 자동 발견 후 검토가 필요한 대상 |
| `deprecated.yaml` | 제외/폐기 대상 |

## 주요 필드

| 필드 | 의미 |
| --- | --- |
| `collection_category` | DB/API에서 사용하는 수집 카테고리 |
| `domain_category` | 화면 표시용 카테고리. 현재는 `collection_category`와 같은 값 |
| `source_group` | 크롤러 운영 그룹 |
| `operator_type` | 운영 주체 |
| `collection_type` | 수집 방식 |
| `crawler_status` | 개발/운영 상태 |

`crawler_status`는 `candidate -> needs_discovery/needs_parser -> partial -> ready` 흐름으로 관리한다. 자동수집 스케줄에는 기본적으로 `ready` 대상만 넣는 것을 원칙으로 한다.

## DB 컬럼

`courses` 테이블에 다음 컬럼을 추가한다.

| 컬럼 | 용도 |
| --- | --- |
| `collection_category` | 강좌의 수집 카테고리 |
| `domain_category` | 사용자 화면 카테고리 |
| `source_group` | 수집 그룹 |
| `operator_type` | 운영 주체 |
| `collection_type` | 수집 방식 |

`category_raw`와 `ai_category`는 강좌 내용 분류로 계속 유지한다.

## 로컬 적용

```powershell
cd C:\project\mooncen
python -X utf8 tools\split_crawl_targets_by_category.py
python -X utf8 DB\setup_db.py --mode migrate
python -X utf8 tools\maintenance\backfill_course_categories.py
```

검증:

```powershell
python -m py_compile tools\split_crawl_targets_by_category.py tools\maintenance\backfill_course_categories.py
```

## 운영 적용

배포 후 운영 서버에서 마이그레이션과 백필을 실행한다.

```bash
cd /opt/mooncen
sudo -u mooncen /opt/mooncen/.venv/bin/python -X utf8 DB/setup_db.py --mode migrate
sudo -u mooncen /opt/mooncen/.venv/bin/python -X utf8 tools/maintenance/backfill_course_categories.py
```

운영 크롤러 1회 실행:

```bash
mooncenctl crawler-once
```

`run_crawlers.py`는 크롤러 사이클 종료 후 기본적으로 `tools/maintenance/backfill_course_categories.py`를 실행한다. 임시로 끄려면 다음 옵션을 사용한다.

```bash
python -X utf8 run_crawlers.py --providers HOMEPLUS --once --skip-category-backfill
```
