# 인천광역시 동구체육회 프로그램예약 크롤러

## 대상

- Provider: `MUNI_WWW_IDGSPORTS_OR_KR_8157C7B5`
- URL: `https://www.idgsports.or.kr/program/programInfoList.do?prgmdiv=pingpong`
- 분류: 체육/스포츠
- Crawler: `Crawler/generated_yaml/MUNI_WWW_IDGSPORTS_OR_KR_8157C7B5.py`

## 구현 내용

- 목록 `programInfoList.do`의 `ul.eduList > li` 카드에서 제목, 대상, 수강료, 교육기간, 신청기간, 정원, 상태, 이미지, 상세 URL을 수집한다.
- 상세 `programInfoDetail.do`의 `dl` 항목에서 교육 요일, 교육 시간, 강사명, 재료비, 강의실, 문의처를 보강한다.
- 교육기간 종료일이 지난 강좌는 기본 수집에서 제외한다.
- 강의실에 `송림골꿈드림센터`가 포함되면 지점명을 `송림골꿈드림센터`로 분리하고 주소를 `인천광역시 동구 새천년로 93`으로 저장한다.

## 수집 필드

- `title`: 목록/상세 제목
- `branch`: 상세 강의실 기반 지점명
- `address`: 지점 주소 매핑
- `period`: 교육기간
- `schedule_raw`: 교육기간 + 요일 + 시간
- `target`: 교육 대상
- `fee`: 기본 수강료
- `material_note`: 재료비/준비물
- `status`: 접수상태
- `description`: 상세 본문
- `image_url`: 프로그램 이미지
- `raw_url`: 상세 URL

## 검증

2026-06-09:

```bash
python -m py_compile Crawler/generated_yaml/MUNI_WWW_IDGSPORTS_OR_KR_8157C7B5.py
python -X utf8 Crawler/generated_yaml/MUNI_WWW_IDGSPORTS_OR_KR_8157C7B5.py --limit 10 --max-pages 3 --timeout 30
python -X utf8 run_crawlers.py --providers MUNI_WWW_IDGSPORTS_OR_KR_8157C7B5 --limit 10 --once --ignore-active-window --ignore-worker-lock --skip-coordinate-backfill --skip-category-backfill
```

결과:

- Collected: 3
- Saved: 3
- Quality: A / 100.0
- 현재 교육기간 미종료 강좌는 3건이며, 접수상태는 모두 마감이라 `CLOSED`로 저장된다.

## 참고

- 송림골꿈드림센터 주소는 공개 검색 결과의 `인천광역시 동구 새천년로 93` 주소를 기준으로 매핑했다.
