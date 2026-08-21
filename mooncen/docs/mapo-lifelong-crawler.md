# 마포구 평생학습포털 강좌 크롤러

## 대상
- Provider: `MUNI_WWW_MAPO_GO_KR_7852A077`
- URL: `https://www.mapo.go.kr/site/mll/edu/lecture_list`
- 분류: 평생학습
- 운영 주체: 마포구청 평생학습포털

## 수집 방식
- 목록 페이지의 강좌 테이블을 파싱한다.
- 페이지 이동은 `cp`, `pageSize`, `listType` GET 파라미터로 처리한다.
- 상세 페이지 `lecture_view?ltSeq=...`를 추가 요청해서 강좌정보 테이블과 강좌소개 영역을 보강한다.
- 교육기간 종료일이 지난 강좌는 기본 수집에서 제외한다.

## 지점/주소 처리
- `교육동`과 `교육장소`를 기준으로 지점을 분리한다.
- 마포구청, 마포구 보건소, 마포구평생학습센터, 염리종합사회복지관은 정적 주소 매핑을 사용한다.
- 그 외 장소는 마포구 장소명 또는 마포구청 기본 주소를 fallback으로 사용한다.

## 수집 필드
- `title`
- `branch`
- `address`
- `period`
- `schedule_raw`
- `target`
- `fee`
- `status`
- `description`
- `image_url`
- `instructor`
- `capacity_total`
- `capacity_current`
- `waitlist_total`
- `reception_period`

## 품질
- 샘플 수집: 10건
- DB 저장: 10/10 성공
- 품질 점수: 96.0
- 누락 필드: `image_url` 일부. 상세 소개에 이미지가 없는 강좌는 이미지가 비어 있다.

## 실행 명령
```bash
python -X utf8 Crawler/generated_yaml/MUNI_WWW_MAPO_GO_KR_7852A077.py --save-db
```

```bash
python -X utf8 run_crawlers.py --providers MUNI_WWW_MAPO_GO_KR_7852A077 --once --ignore-active-window
```
