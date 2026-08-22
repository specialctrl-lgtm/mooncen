# 성북구청 통합예약 크롤러

## 대상

- Provider: `MUNI_WWW_SB_GO_KR_FF615DE7`
- Site: `https://www.sb.go.kr/yeyak/selectUnityProgrmWebList.do`
- Source group: `public_reservation`
- Parser: `sb_unity_program_categories`

## 수집 방식

- 성북 통합예약의 교육/강좌 중 자치회관 목록을 기준으로 수집한다.
- `searchProgrmSe` 카테고리를 순환한다.
- 목록은 `viewType=list` 테이블을 사용한다. 테이블에는 제목, 접수기간, 교육기간, 운영기관, 교육요일, 신청/정원, 상태가 있다.
- 상세 링크는 `javascript:fnView(this, {progrmNo})`에서 `progrmNo`를 추출해 `unityProgrmWebView.do`로 요청한다.
- 상세 페이지에서 대상, 장소, 교육시간, 수강료, 문의전화, 주소를 보강한다.

## 카테고리

- 전체
- 정보화(IT)
- 어학/인문
- 미술/서예
- 노래/댄스
- 취미/공예
- 건강/뷰티
- 음악/악기
- 자격증
- 요리
- 농업
- 특강
- 기타
- 자기개발
- 건강체력
- 수리과학
- 창의체험

## 실행

샘플:

```bash
python -X utf8 Crawler/generated_yaml/MUNI_WWW_SB_GO_KR_FF615DE7.py --per-target-limit 10 --max-pages 2 --detail-limit 10
```

운영 품질 우선:

```bash
python -X utf8 Crawler/generated_yaml/MUNI_WWW_SB_GO_KR_FF615DE7.py --save-db --max-pages 30 --detail-limit 1200
```

## 검증 결과

- 10건 샘플: 10건 수집, title/period/schedule/fee 10/10
- 전체 순환: 1,014건 수집, 57페이지 탐색
- 개발 DB 저장 실행: 1,014건 수집, 624건 저장. 교육기간이 지난 항목은 저장 단계에서 제외됐다.
- `--detail-limit 40` 기준 수강료/주소/대상은 40건만 채워진다. 성북은 금액과 정확한 주소가 상세 페이지에 있으므로 운영 수집에서는 상세 제한을 충분히 크게 둬야 한다.
- 운영내용(description)은 샘플 상세 페이지에서 비어 있어 0건으로 확인됐다.
