# ULSAN_EDU_BOOKING Crawler

## Target
- Provider: `ULSAN_EDU_BOOKING`
- Name: 울산광역시교육청 통합예약
- URL: `https://use.go.kr/booking/user/reservation/Edu/BD_selectReservationMngList.do`
- Category: 평생학습 / 평생교육

## Parser
- Implementation: `Crawler/Crawler_MunicipalYaml.py`
- Parser name: `ulsan_edu_booking_list+detail`
- List selector: `.prgr-booking-list ul.list > li.item`
- Detail URL: `BD_selectReservationMng.do?q_rsvtRegSn={id}`
- Pagination: `q_currPage`, `opMovePage(n)` 기준으로 74페이지 확인

## Collected Fields
- `title`: 목록 카드 제목
- `provider_course_id`: `opViewReservation(...)`의 예약 일련번호
- `branch`: 목록의 `기관`
- `category_raw`: `평생교육`
- `target`: 목록의 `대상`
- `apply_period`: 목록의 `신청기간`
- `period`, `schedule_raw`: 목록의 `운영기간`
- `status`: 카드 상태 배지
- `capacity`: 목록의 `모집인원`
- `venue_name`: 목록의 `장소`
- `venue_address`: 장소가 주소 패턴일 때만 저장
- `contact`: 목록의 `문의전화`
- `description`: 상세의 `소개` 섹션, 없으면 목록 카드 텍스트
- `image_url`: 목록/상세 이미지
- `raw_url`, `application_url`: 상세 페이지 URL
- `collection_category`, `domain_category`: 기본 `평생학습`; 공식 기관명이
  과학관·미술관·박물관·도서관이면 해당 기관 유형
- `source_group`: 기본 `lifelong_learning`; 위 기관 행은
  `museum_science` 또는 `library`
- `service_group`: 위 기관 행은 `체험`으로 고정하고 나머지는 프로그램
  내용 기반으로 분류

## Latest Verification
- Run time: `2026-07-23T23:06:20+09:00`
- Command:

```bash
python -X utf8 Crawler/generated_yaml/ULSAN_EDU_BOOKING.py --dry-run --per-target-limit 0 --max-pages 100 --detail-limit 0 --timeout 20
```

- Report: `logs/municipal_crawler_reports/municipal_yaml_crawler_20260723_230620_550597_22620.yaml`
- Official catalogue: 2,298 rows / 77 pages
- Current or future rows retained: 789
- Expired rows excluded before detail collection: 1,509
- Detail pages sampled by the dry-run: 1
- Saved: 0 (`--dry-run`)

## Classification Verification
- Checked at: `2026-06-05T01:01:58+09:00`
- Provider name: `울산광역시교육청 통합예약`
- `category_raw`: `평생교육`
- `collection_category`, `domain_category`: `평생학습`
- `source_group`: `lifelong_learning`
- Registry status: `ready`, enabled

## Notes
- 사이트 목록에 수강료 필드가 노출되지 않아 `fee`는 비어 있다.
- 운영기간이 지난 강좌는 저장 단계에서 제외된다.
- 상태가 마감이어도 운영기간이 남아 있으면 DB에는 보존되며, 화면 기본 필터에서 제외하는 정책을 따른다.
- `use.go.kr`이 TLS 중간 인증서를 전송하지 않는 현 상태는 정확한 호스트에
  한해 공개 Sectigo 중간 인증서를 보완하며, 인증서·호스트 이름 검증은 계속
  활성화한다.
