# 양주시 통합예약 교육강좌 크롤러

## 대상
- Provider: `MUNI_WWW_YANGJU_GO_KR_E168EB3A`
- URL: `https://www.yangju.go.kr/yeyak/selectEduLctreWebList.do?key=2148&rcritTrget=adult&searchEduInsttNo=&searchEduPlaceNo=&searchEduSttus=&searchRceptBgnde=&searchRceptEndde=&pageUnit=10&searchCnd=all&searchKrwd=&pageIndex=1`
- 분류: `공공예약`
- 파서: `yangju_edu_lecture_list+detail`

## 수집 방식
- 목록 페이지의 `table.list_table` 행을 수집한다.
- 목록의 `eduLctreWebView.do` 링크를 상세 URL로 사용한다.
- 상세 페이지의 `.education_request li.clearfix` 안의 `em` 라벨과 `p` 값을 라벨/값 구조로 파싱한다.
- `교육기간` 종료일이 오늘보다 이전인 강좌는 수집 단계에서 제외한다.
- 현재/미래 강좌만 저장하고 `--mark-stale` 실행 시 이전 generic 수집 active 데이터는 비활성화한다.

## 필드 매핑
- `title`: 상세 제목
- `branch`: 상세 `교육기관`
- `venue_name`, `room`: 상세 `교육장소`, `강의실`
- `period`: 상세 `교육기간`
- `apply_period`: 상세 `접수기간`
- `schedule_raw`: 목록 `교육일시 시간`
- `target`: 상세 `수강대상`
- `fee`: 상세 `수강료`
- `status`: 상세 접수상태
- `description`: 상세 `강의개요`, `유의사항`
- `capacity_total`, `capacity_current`, `waitlist_total`: 목록/상세 모집인원
- `instructor`, `phone`, `application_url`, `application_method_raw`: 상세 강사명, 전화번호, 신청 URL, 접수방식/모집방법

## 주소 보정
상세 페이지는 교육기관명만 제공하므로 주요 지점 주소를 파서에서 직접 보정한다.

- `드론봇인재교육센터`: 경기도 양주시 광적면 부흥로 847 양주테크노시티 218호
- `양주시청년센터`: 경기도 양주시 부흥로 1533
- `양주시립민복진미술관`: 경기도 양주시 장흥면 권율로 192
- `도시환경사업소`: 경기도 양주시 평화로 1920
- `양주시농업기술센터`: 경기도 양주시 광적면 지섬로 162

## 검증 결과
- 2026-06-05 기준 현재/미래 교육강좌: 12건
- 저장 결과: 12건 저장
- 활성 지점: 3개
- 품질 등급: A
- 핵심 필드 채움률: 100%
- 중요 필드 채움률: 100%
- 활성 지점 좌표: 3개 모두 Google Geocoding 검증 완료

## 실행
```bash
python -X utf8 Crawler/generated_yaml/MUNI_WWW_YANGJU_GO_KR_E168EB3A.py --per-target-limit 0 --max-pages 10 --detail-limit 100 --save-db --mark-stale
```
