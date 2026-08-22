# Daedeok Lifelong Center Crawler

## Provider

- Provider: `MUNI_WWW_DAEDEOK_GO_KR_F1987640`
- Name: `대전광역시 대덕구청`
- Source URL: `https://lll.daedeok.go.kr/lms/damoa/contents/dms/edu/07/edu.07.001.motion?mnucd=MENU0100097`
- Scope: 대덕구 평생학습관 프로그램신청

## Parser

- List parser: `table.simple tbody tr`
- Detail access: POST `/lms/damoa/contents/dms/edu/07/edu.07.001.motion`
- Detail mode: `bmode=detail1`
- Detail parser: `.board_view ul.detail > li`
- Detail fields:
  - title: `프로그램명`
  - period: `교육기간`
  - schedule_raw: `교육일정`
  - target: `교육대상`
  - fee: `수강료`
  - material_fee: `수강료외 부대비용`
  - material_note: `학습자준비물`
  - capacity_text: `모집인원`
  - instructor: `강 사 명`
  - branch: `대덕구 평생학습관` + `교육장소`
  - description: `강의목표`, `강의내용`, `강좌속성`, `모집방법`, `모집제한`

## Branch

- Base branch: `대덕구 평생학습관`
- Address: `대전광역시 대덕구 대덕대로 1579, 3층`
- Classroom names from `교육장소` are appended to the branch name.

## Rules

- Courses whose education end date is earlier than the current date are skipped.
- The source does not expose course-specific images, so `image_url` remains empty.
- The crawler accepts `--limit`, `--max-pages`, `--save-db`, `--include-expired`, and worker compatibility args.

## Commands

```bash
python -X utf8 Crawler/generated_yaml/MUNI_WWW_DAEDEOK_GO_KR_F1987640.py --limit 10 --max-pages 2
python -X utf8 Crawler/generated_yaml/MUNI_WWW_DAEDEOK_GO_KR_F1987640.py --save-db --limit 10
python -X utf8 run_crawlers.py --providers MUNI_WWW_DAEDEOK_GO_KR_F1987640 --limit 10 --once --ignore-active-window --ignore-worker-lock --skip-coordinate-backfill --skip-category-backfill
```

## Validation

| Date | Rows | Saved | Score | Notes |
|---|---:|---:|---:|---|
| 2026-06-08 | 10 | 10 | 90.0 | All core fields filled; no source image. |

