# 통영시 평생학습 강좌정보 크롤러

## 대상
- Provider: `MUNI_WWW_TONGYEONG_GO_KR_DC5CDBF8`
- URL: `https://www.tongyeong.go.kr/tylearning/04266/04267/05286.web`
- 분류: 평생학습
- 운영 주체: 통영시 평생학습도시

## 수집 방식
- 목록의 `table.t3` 행에서 기관, 강좌명, 모집 인원, 접수 기간, 강좌 기간, 접수 방법, 수강료, 상태를 수집한다.
- 상세 URL은 `?amode=view&idx=...` 형태로 구성한다.
- 상세 테이블에서 교육기간, 교육장소, 접수기간, 수강료, 모집대상, 접수방법, 이용문의, 첨부파일을 보강한다.
- 상세 이미지 중 `ImagePrint.do` 또는 강좌 사진 alt가 있는 이미지를 `image_url`로 저장한다.
- TLS 인증서 검증은 항상 유지한다. 로컬에서 인증서 체인 오류가 나면 OS/Python CA 저장소를 갱신하고 서버 체인을 점검하며, 검증 우회 옵션은 사용하지 않는다.
- 교육기간 종료일이 지난 강좌는 기본 수집에서 제외한다.

## 품질
- 종료 강좌 포함 샘플 수집: 10건
- 품질 점수: 97.0
- 기본 DB 저장: 0건
- 사유: 현재 목록 상위 강좌가 모두 교육기간 종료 상태이며, 종료 강좌는 기본 저장 대상에서 제외한다.

## 실행 명령
```bash
python -X utf8 Crawler/generated_yaml/MUNI_WWW_TONGYEONG_GO_KR_DC5CDBF8.py --save-db
```

종료 강좌 포함 품질 확인:
```bash
python -X utf8 Crawler/generated_yaml/MUNI_WWW_TONGYEONG_GO_KR_DC5CDBF8.py --include-expired --limit 10
```

통합 실행:
```bash
python -X utf8 run_crawlers.py --providers MUNI_WWW_TONGYEONG_GO_KR_DC5CDBF8 --once --ignore-active-window
```
