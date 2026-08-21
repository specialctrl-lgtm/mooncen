# 수성구 평생교육 플랫폼 러닝톡 메뉴 URL 크롤러

## 대상

- Provider: `MUNI_LLL_SUSEONG_KR_F59F7BFE`
- URL: `https://lll.suseong.kr/index.do?menu_id=00001969`
- 분류: 평생학습

## 구현 내용

- 이 URL은 `MUNI_LLL_SUSEONG_KR_2C82AF9F`의 수강신청 목록과 동일한 화면을 메뉴 ID만으로 노출한다.
- 중복 구현을 피하기 위해 `MUNI_LLL_SUSEONG_KR_2C82AF9F` 파서를 재사용하고, provider와 목록 URL만 이 provider 기준으로 재설정한다.
- 목록/상세 수집 방식은 수강신청 크롤러와 동일하다.
- 교육기관 기준 지점 분리, 상세 주소/문의전화/강좌소개 보강, 교육기간 종료 강좌 제외 정책을 그대로 적용한다.

## 품질 확인

2026-06-08 개발 DB 기준:

| 항목 | 결과 |
| --- | --- |
| collected | 5 |
| saved | 5 |
| score | 100.0 |
| grade | A |
| parser | suseong_learning_menu_wrapper |

참고: 10건 샘플 수집은 성공했으나 DB 저장 검증 중 일시적인 사이트 연결 타임아웃이 있어, 재시도에서 5건 저장 기준으로 검증했다.

## 실행

```bash
python -X utf8 Crawler/generated_yaml/MUNI_LLL_SUSEONG_KR_F59F7BFE.py --limit 10 --max-pages 2
python -X utf8 Crawler/generated_yaml/MUNI_LLL_SUSEONG_KR_F59F7BFE.py --save-db
```
