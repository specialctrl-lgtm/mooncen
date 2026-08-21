# Frontend Application Button And Fee Display

## 변경일
- 2026-06-09

## 변경 내용
- 강좌 상세 모달의 `원문보기` 버튼명을 `수강신청`으로 변경했다.
- 강좌 목록 카드의 비용 표시를 `총 비용` 중심으로 변경했다.
- 수강료와 재료비는 총 비용 아래에 각각의 금액으로 함께 표시한다.
- 강좌 상세 모달에서는 `비용` 카드 하나에 합계, 수강료, 재료비를 함께 표시한다.
- 모바일에서 금액 텍스트가 잘리지 않도록 비용 영역의 줄바꿈과 폭 제약을 조정했다.

## 관련 파일
- `frontend2/src/components/ClassCard.tsx`
- `frontend2/src/components/CourseDetailModal.tsx`
- `frontend2/src/styles.css`

## 검증
- `npm run build` 통과
