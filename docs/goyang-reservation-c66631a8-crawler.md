# 고양시 통합예약 일산동구 크롤러

## 대상

- Provider: `MUNI_WWW_GOYANG_GO_KR_C66631A8`
- URL: `https://www.goyang.go.kr/resve/manage/BD_selectResveManageList.do?q_resveTopClCode=CL_01&q_guDeptCode=396010000`
- 분류: 교육·체험 / 공공예약
- 지점 필터: `q_guDeptCode=396010000` 일산동구

## 구현 내용

- 검증된 고양시 통합예약 전용 파서 `MUNI_WWW_GOYANG_GO_KR_9C1A7354`를 재사용한다.
- provider, provider name, 기본 branch, `GU_CODES`를 일산동구 전용 값으로 주입한다.
- 목록 `BD_selectResveManageList.do`에서 신청 항목을 찾고 상세 `BD_selectResveManage.do`에서 기간, 대상, 수강료, 설명, 이미지, 장소 정보를 보강한다.
- 장소가 일산동구청 등 알려진 장소이면 주소 매핑을 적용해 지도 표시가 가능하도록 저장한다.
- 교육기간 종료일이 지난 강좌는 기본 수집에서 제외한다.

## 품질 확인

2026-06-09 개발 DB 기준:

| 조건 | collected | saved | score | grade |
| --- | ---: | ---: | ---: | --- |
| 기본 수집 | 2 | 2 | 100.0 | A |

필드 `title`, `branch`, `address`, `period`, `schedule_raw`, `target`, `fee`, `status`, `description`, `image_url`이 모두 채워졌다.

## 실행

```bash
python -X utf8 Crawler/generated_yaml/MUNI_WWW_GOYANG_GO_KR_C66631A8.py --limit 10 --max-pages 3 --timeout 30
python -X utf8 run_crawlers.py --providers MUNI_WWW_GOYANG_GO_KR_C66631A8 --limit 10 --once --ignore-active-window --ignore-worker-lock --skip-coordinate-backfill --skip-category-backfill
```
