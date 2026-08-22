# 강동구 평생학습관 프로그램신청 크롤러

## 대상

- Provider: `MUNI_LLL_GANGDONG_GO_KR_E8F6E943`
- URL: `https://lll.gangdong.go.kr/program/ProgramBoardList.do?menucode=84`
- 분류: 평생학습

## 구현 내용

- 목록 `ProgramBoardList.do`의 프로그램 표에서 강의명, 신청기간, 교육기간, 강의시간, 수강료, 신청/정원, 상태, 이미지를 수집한다.
- `fn_view(gn_seq)`에서 상세 ID를 추출하고 `ProgramClassroomView.do`에 POST로 상세를 요청한다.
- 상세에서 분류, 강의명, 수강료, 신청현황, 접수기간, 강의기간, 강의시간, 교육장소, 담당자/문의, 강사명, 강사소개를 보강한다.
- 교육장소를 지점명으로 분리한다.
- 상세주소는 제공되지 않아 강동구 기본주소를 fallback으로 저장한다.
- 교육기간 종료일이 지난 강좌는 기본 수집에서 제외한다.

## 품질 확인

2026-06-08 개발 DB 기준 기본 수집 및 저장 결과:

| 항목 | 결과 |
| --- | --- |
| collected | 5 |
| saved | 5 |
| score | 100.0 |
| grade | A |
| parser | gangdong_program_table+detail |

필수 필드 `title`, `branch`, `address`, `period`, `schedule_raw`, `target`, `fee`, `status`, `description`, `raw_url` 모두 채워졌다.

## 실행

```bash
python -X utf8 Crawler/generated_yaml/MUNI_LLL_GANGDONG_GO_KR_E8F6E943.py --limit 10 --max-pages 2
python -X utf8 Crawler/generated_yaml/MUNI_LLL_GANGDONG_GO_KR_E8F6E943.py --save-db
```
