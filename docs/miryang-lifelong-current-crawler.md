# 밀양시평생학습포털 진행중 강좌 크롤러

## 대상
- Provider: `MUNI_WWW_MIRYANG_GO_KR_590AFA4C`
- URL: `https://miryang.go.kr/edu/nmprogram/curriculum/default.php?st=e`
- 분류: 평생학습
- 기본 지점: `밀양시 미래교육과`
- 기본 주소: `경상남도 밀양시 중앙로 265`

## 수집 방식
- 목록 페이지의 `table.basic_edu` 행을 읽어 강좌명, 분야, 기관, 모집 인원, 접수 기간, 교육 기간, 상태를 수집한다.
- 상세 페이지 `mod=o&idx=...`를 추가 요청해서 상세 테이블을 파싱한다.
- 상세의 여러 `h2` 중 `강좌명:`으로 시작하는 노드만 강좌명으로 사용한다.
- 상세 `수강료` 칸에 사이트 PHP Warning 문구가 표시되는 경우 값으로 사용하지 않고, `교육과정` 본문 안의 `수 강 료:` 라벨에서 보정한다.
- 교육기간 종료일이 지난 강좌는 기본 수집에서 제외한다.

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
- `raw_url`
- `venue_name`
- `phone`

## 품질
- 샘플 수집: 2건
- DB 저장: 2/2 성공
- 품질 점수: 90.0
- 누락 필드: `image_url`
- 비고: 현재 사이트 상세에는 강좌 대표 이미지가 없어 이미지 누락은 정상 한계로 처리한다.

## 실행 명령
```bash
python -X utf8 Crawler/generated_yaml/MUNI_WWW_MIRYANG_GO_KR_590AFA4C.py --save-db
```

```bash
python -X utf8 run_crawlers.py --providers MUNI_WWW_MIRYANG_GO_KR_590AFA4C --once --ignore-active-window
```
