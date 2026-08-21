# 부산 사상구 통합예약 크롤러

## 대상
- Provider: `SASANG_RESERVATION`
- URL: `https://www.sasang.go.kr/user/apply/list.sasang?menuCd=DOM_000001003016000000&contentsSid=1650&cpath=%2Fyeyak`
- 분류: `공공예약`
- 파서: `sasang_apply_list+detail`

## 수집 방식
- 목록 페이지의 `.bbs_edu > ul > li` 항목을 수집한다.
- 제목 링크의 `url_chk(...)` 값을 이용해 상세 URL인 `/user/apply/view.sasang`를 복원한다.
- `searchDateType=eduDate`, `searchStartDate=오늘`, `searchEndate=오늘+370일` 조건을 기본 적용해 교육기간이 지난 항목은 제외한다.
- 상세 페이지의 `.edu_vtype .infor span.name` 라벨/값과 `.edu_vtype .infor_con` 설명 섹션을 병합한다.

## 필드 매핑
- `title`: 상세 페이지 강좌명
- `branch`: 교육기관 기준 기관명
- `room`, `venue_name`: 교육장소/강의실
- `address`, `venue_address`: 기관명/장소 기준 보정 주소
- `period`: 강좌기간
- `apply_period`: 접수기간
- `schedule_raw`: 교육시간
- `target`: 교육대상
- `fee`: 수강료
- `material_fee`, `material_note`: 준비물/재료비
- `status`: 접수상태
- `description`: 과정개요, 학습목표, 기타사항
- `capacity_total`, `capacity_current`, `waitlist_total`: 모집/신청/대기 인원
- `application_method_raw`, `phone`, `instructor`: 접수방법, 문의전화, 강사명

## 주소 보정
사상구 상세 페이지는 일부 장소가 `도서관`, `동주민센터`, `생활사박물관`처럼 넓은 기관명으로만 제공된다. 지도 표시는 아래 기관 주소를 우선 사용한다.

- `주례열린도서관`: 부산광역시 사상구 주례로 110
- `덕포1동 행정복지센터`: 부산광역시 사상구 강선로 31
- `사상생활사박물관`: 부산광역시 사상구 낙동대로1258번길 36
- `주례3동 행정복지센터`: 부산광역시 사상구 냉정로 10
- `사상구보건소`: 부산광역시 사상구 학감대로 242
- `사상도서관`: 부산광역시 사상구 덕상로72번길 9

## 검증 결과
- 2026-06-05 기준 현재/미래 교육기간 대상: 17건
- 저장 결과: 17건 저장
- 품질 등급: A
- 핵심 필드 채움률: 100%
- 중요 필드 채움률: 100%
- 활성 강좌 지점: 6개 기관
- 좌표 보정: 활성 강좌 지점 6개 모두 Google Geocoding 기준 좌표 검증 완료

## 실행
```bash
python -X utf8 Crawler/generated_yaml/SASANG_RESERVATION.py --per-target-limit 0 --max-pages 10 --detail-limit 100 --save-db --mark-stale
```
