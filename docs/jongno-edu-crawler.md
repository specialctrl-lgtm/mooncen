# 종로구청 평생학습 크롤러

## 대상
- Provider: `MUNI_WWW_JONGNO_GO_KR_728A2F6A`
- URL: `https://www.jongno.go.kr/edu/eduApplyList.do`
- 분류: 평생학습
- Parser: `jongno_edu_apply`

## 구현 내용
- 목록 페이지의 각 행에서 `edu_open_cd` hidden 값을 읽는다.
- 상세 페이지는 `GET`이 아니라 `POST /edu/eduApplyview.do` 구조라서 `edu_open_cd`, `pageIndex`를 본문으로 전송한다.
- 상세 테이블에서 `수강료`, `교재비`, `교육장소`, `상태`, `교육문의`, `내용`을 수집한다.
- `교육기간` 값이 `06.26 ~ 06.26 금 11:00 ~ 13:00` 형태로 섞여 있어 날짜와 시간을 분리한다.
- 상세 설명의 `65세 이상 ...` 문장을 대상 필드로 보강한다.

## 2026-06-04 실행 결과
- 수집: 1,385건
- DB 저장: 864건
- 페이지: 70
- 상세 요청: 1,385건
- 필드 채움: title/period/schedule/fee/description 100%
- 대상 필드: 720/1,385건
- 기존에 잘못 저장된 상세 URL 없는 활성 데이터 20건은 비활성화했다.
- 리포트: `logs/municipal_crawler_reports/municipal_yaml_crawler_20260604_234100.yaml`

## 실행 명령
```bash
python -X utf8 Crawler/generated_yaml/MUNI_WWW_JONGNO_GO_KR_728A2F6A.py --save-db
```
