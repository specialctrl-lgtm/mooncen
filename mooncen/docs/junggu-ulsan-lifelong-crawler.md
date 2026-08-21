# 울산 중구 평생학습관 크롤러

## 대상

- Provider: `MUNI_WWW_JUNGGU_ULSAN_KR_9703AC0F`
- URL: `https://www.junggu.ulsan.kr/edu/onRequest/selectProgram.do`
- 분류: 평생학습
- 크롤러: `Crawler/generated_yaml/MUNI_WWW_JUNGGU_ULSAN_KR_9703AC0F.py`

## 구조

- 목록은 `GET /edu/onRequest/selectProgram.do?exec=list&currentPage={page}&pagePerCount=15` 형식이다.
- 목록의 각 강좌는 `.register_list > ul > li` 아래 `fn_view('PRG_...')` onclick 값으로 상세 ID를 가진다.
- 상세는 같은 엔드포인트에 `exec=view&prgId={program_id}` 파라미터로 접근한다.
- 상세 테이블에서 교육대상, 접수기간, 교육기간, 교육시간, 강사명, 수강료, 재료비, 강좌소개를 수집한다.

## 수집 필드

- `title`: 상세 `강좌명`
- `branch`: 제목/기관명에서 평생학습센터, 중구문화대학 등을 추론
- `address`: 기본값 `울산광역시 중구 중앙길 136`
- `period`: 상세 `교육기간`
- `schedule_raw`: `교육기간 + 교육시간`
- `target`: 상세 `교육대상`, 없으면 `주민`
- `fee`: 상세 `수강료`
- `material_fee`, `material_note`: 상세 `재료(교재)비`
- `status`: 목록 상태 라벨을 `OPEN/SCHEDULED/CLOSED`로 정규화
- `description`: 상세 `강좌소개`
- `image_url`: 사이트에서 강좌 이미지를 제공하지 않아 기본적으로 비어 있음

## 실행

```bash
python -X utf8 Crawler/generated_yaml/MUNI_WWW_JUNGGU_ULSAN_KR_9703AC0F.py --limit 10 --max-pages 3
python -X utf8 Crawler/generated_yaml/MUNI_WWW_JUNGGU_ULSAN_KR_9703AC0F.py --limit 10 --max-pages 3 --save-db
```

## 검증 결과

- 2026-06-08 샘플 10건 수집 성공
- DB 저장 10/10 성공
- 품질 점수 90.0
- 필드 카운트: title/branch/address/period/schedule_raw/target/fee/status/description 10/10, image_url 0/10
- 교육기간 종료 강좌는 기본 수집에서 제외한다.
