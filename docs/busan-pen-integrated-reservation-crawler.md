# 부산광역시교육청 통합예약포털 견학체험 크롤러

## 대상

- Provider: `MUNI_HOME_PEN_GO_KR_92635850`
- URL: `https://home.pen.go.kr/yeyak/exprn/selectExprnList.do?mi=14438`
- 크롤러: `Crawler/generated_yaml/MUNI_HOME_PEN_GO_KR_92635850.py`
- 분류: 공공예약 / 교육체험

## 정리 방향

- 기존 동래교육지원청 게시판성 URL은 통합 예약 URL로 대체한다.
- 기관별 URL을 provider로 분리하지 않는다.
- 통합 목록의 `기관명`을 `branch`로 저장해서 지도/목록에서 지점별로 분기한다.
- 지점 주소는 `facility_registry_crawl_targets.yaml`과 정적 주소 힌트를 우선 사용하고, 없으면 부산광역시교육청 기본 주소를 사용한다.

## 구조

- 목록: `/yeyak/exprn/selectExprnList.do`
- 주요 파라미터:
  - `mi=14438`
  - `currPage`
  - `pageIndex`
  - `srchRsvSttus=REQST`
  - `srchRsvSttus=PREV`
- 상세: `/yeyak/exprn/selectExprnInfo.do?mi=14438&exprnSeq={id}&exprnPeriodSeq={period_id}`
- 목록 행의 `data-id`, `data-period-id`, `data-rssysid`를 상세 및 고유키로 사용한다.

## 수집 필드

- `title`: 체험명과 회차/부제
- `branch`: 목록/상세의 운영기관
- `address`: 시설 레지스트리 또는 정적 주소 힌트
- `period`: 운영기간
- `schedule_raw`: 운영기간 기반 일정
- `target`: 체험대상
- `fee`: 기본 무료
- `status`: 접수중은 `OPEN`, 예정은 `SCHEDULED`, 마감/종료는 `CLOSED`
- `description`: 상세의 이용안내/체험안내/유의사항 콘텐츠
- `capacity_current`, `capacity_total`, `waitlist_total`: 상세 달력의 `[현재/정원] (대기 현재/정원)` 패턴에서 추출

## 실행

```bash
python -X utf8 Crawler/generated_yaml/MUNI_HOME_PEN_GO_KR_92635850.py --limit 10 --max-pages 2
python -X utf8 Crawler/generated_yaml/MUNI_HOME_PEN_GO_KR_92635850.py --limit 10 --max-pages 2 --save-db
```

## 검증 결과

- 2026-06-08 샘플 10건 수집 성공
- DB 저장 10/10 성공
- 품질 점수 90.0
- 샘플 10건에서 branch 3개로 분기 확인
- `image_url`은 목록/상세에서 별도 강좌 이미지가 없어 0/10
