# 원주시 통합예약 교육강좌 크롤러

## 대상

- Provider: `MUNI_WWW_WONJU_GO_KR_56B0C690`
- URL: `https://yeyak.wonju.go.kr/www/eduLectureAllWebList.do?key=74`
- 분류: 교육·체험 / 평생학습
- Crawler: `Crawler/generated_yaml/MUNI_WWW_WONJU_GO_KR_56B0C690.py`

## 구현 내용

- 목록 `eduLectureAllWebList.do`의 `li.thumbnail_item` 카드에서 제목, 접수상태, 지역, 장소, 대상, 접수기간, 운영기간, 이미지, 상세 URL을 수집한다.
- 상세 `eduLectureWebView.do`의 테이블에서 운영기관, 카테고리, 과목, 대상, 장소, 주소, 접수기간, 운영기간, 운영시간, 운영요일, 모집/신청, 요금, 교재비, 재료비, 문의전화를 보강한다.
- 지점은 상세 `운영기관` 기준으로 분리하고, 지도 주소는 상세 `주소` 값을 사용한다.
- 교육기간 종료일이 지난 강좌는 기본 수집에서 제외한다.

## 수집 필드

- `title`: 목록 제목
- `branch`: 상세 운영기관
- `address`: 상세 주소
- `period`: 운영기간
- `schedule_raw`: 운영기간 + 운영요일 + 운영시간
- `target`: 대상
- `fee`: 이용요금
- `material_note`, `material_fee`: 교재비/재료비
- `status`: 접수상태
- `description`: 상세 테이블 전체 요약
- `image_url`: 목록/상세 이미지
- `raw_url`: 상세 URL

## 검증

2026-06-09:

```bash
python -m py_compile Crawler/generated_yaml/MUNI_WWW_WONJU_GO_KR_56B0C690.py
python -X utf8 Crawler/generated_yaml/MUNI_WWW_WONJU_GO_KR_56B0C690.py --limit 10 --max-pages 3 --timeout 30
python -X utf8 run_crawlers.py --providers MUNI_WWW_WONJU_GO_KR_56B0C690 --limit 10 --once --ignore-active-window --ignore-worker-lock --skip-coordinate-backfill --skip-category-backfill
```

결과:

- Collected: 10
- Saved: 10
- Quality: A / 100.0
- Required fields were filled for `title`, `branch`, `address`, `period`, `schedule_raw`, `target`, `fee`, `status`, `description`, `raw_url`, and `image_url`.

## 참고

- 기존 registry는 원주시청 공지 URL로 잡혀 있어 비활성 상태였으나, 실제 수집 URL인 `yeyak.wonju.go.kr/www/eduLectureAllWebList.do?key=74`로 교체했다.
- 목록과 상세 모두 UTF-8로 강제 디코딩한다.
