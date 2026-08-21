# 메인 필터 위치 및 동작 보정

## 변경일
- 2026-06-08

## 변경 내용
- 메인 필터가 지도 위가 아니라 지도 아래에 표시되도록 CSS order를 조정했다.
- 모바일에서는 필터 열기 버튼도 지도 아래에 배치하고, 기존 필터 패널 열기/닫기 동작은 유지한다.
- 메인 필터 중 기존에 로드된 결과에만 적용되던 항목을 서버 쿼리에도 반영했다.
  - 연령: `age_groups`
  - 영유아 개월/만나이: `child_age_months`
  - 시간대: `time_groups`
  - 모집상태: `statuses`

## 관련 파일
- `frontend2/src/App.tsx`
- `frontend2/src/api.ts`
- `frontend2/src/styles.css`
- `backend/routers/courses.py`

## 검증
- `python -m py_compile backend/routers/courses.py` 통과
- `npm run build` 통과
