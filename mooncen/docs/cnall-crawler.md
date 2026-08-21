# CNALL Lecture Crawler

Provider: `CNALL_LECTURE`

URL: `https://www.cnall.or.kr/lecture/lectureList.do?organAllYn=Y&areaCd=&targetCd=&lectureDiv1Cd=&lectureDiv2Cd=&costYn=&applyTypeCd=&organAllChk=Y&searchCondition=&searchKeyword=`

## Implementation

- File: `Crawler/generated_yaml/CNALL_LECTURE.py`
- Parser: `cnall_lecture_cards+detail`
- Type: standalone crawler

This crawler does not depend on `Crawler/Crawler_MunicipalYaml.py`.

## Source Structure

The crawler first loads `/organ/organList.do` to build branch metadata:

- branch name
- stable `branch_code` from `organIdxArr`
- address
- phone
- homepage URL

It then crawls `.lecture-list > li` cards from `/lecture/lectureList.do`, follows each `fnDetail('lectureIdx')` detail page, and fills course fields from the detail tables.

## Field Mapping

| Field | Source |
|---|---|
| `title` | list card title |
| `branch` | list card `기관`, normalized against organ list |
| `address` | `/organ/organList.do` branch address |
| `period` | detail `강좌기간` |
| `schedule_raw` | detail `강좌기간` weekday/time |
| `target` | detail `대상` |
| `fee` | detail/list `수강료` |
| `status` | list card status |
| `capacity_total/current/remaining` | list/detail `신청자수` |
| `description` | detail content table |
| `application_url` | reconstructed detail URL |

Most CNALL details do not expose a representative course image, so `image_url` may legitimately be empty.

## Branch Normalization

Long office names are normalized into map-friendly names:

| Source | Stored branch |
|---|---|
| `충청남도교육청남부평생교육원` | `남부평생교육원` |
| `충청남도아산교육지원청아산도서관` | `아산도서관` |
| `충청남도천안교육지원청성환도서관` | `성환도서관` |

## Commands

Sample:

```powershell
python -X utf8 Crawler\generated_yaml\CNALL_LECTURE.py --limit 10 --max-pages 2 --detail-limit 10 --timeout 25
```

Save:

```powershell
python -X utf8 Crawler\generated_yaml\CNALL_LECTURE.py --limit 10 --max-pages 2 --detail-limit 10 --timeout 25 --save-db
```

Full save:

```powershell
python -X utf8 Crawler\generated_yaml\CNALL_LECTURE.py --limit 0 --max-pages 40 --detail-limit 0 --timeout 25 --save-db --mark-stale
```

Coordinate backfill:

```powershell
python -X utf8 tools\maintenance\kakao_geocode_branches.py --provider CNALL_LECTURE --update-all
```

## Latest Verification

2026-06-05 local 10-row sample:

| Rows | Saved | Pages | Detail | Title | Branch | Address | Period | Schedule | Fee | Target | Description | Image | Application URL |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 10 | 10 | 1 | 10 | 10 | 10 | 10 | 10 | 10 | 10 | 10 | 10 | 0 | 10 |

Active DB check after saving:

| Active Courses | Branches | Title | Start Date | Schedule | Fee | Target | Description | Application URL |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 230 | 17 | 230 | 230 | 230 | 230 | 230 | 172 | 230 |

Rows whose education period has already ended are skipped during DB save by default. Use `--include-expired` only for diagnostics.
