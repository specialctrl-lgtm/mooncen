# Frontend Layout Notes

## Course Result Groups

- 강좌 목록은 지점별 그룹을 세로로 쌓고, 각 지점 안의 강좌 카드는 가로 스크롤로 표시한다.
- 지점 그룹 헤더는 `provider badge + 지점명 + 건수`를 한 줄로 표시한다.
- 지점명이 길면 그룹 헤더 높이가 늘어나지 않도록 말줄임 처리한다.
- 모바일에서도 같은 한 줄 구조를 유지하고, 강좌 카드 영역만 가로 스크롤한다.
- 지점 그룹 헤더를 누르면 해당 지점만 펼쳐진 그리드로 표시하고, 다시 누르면 기본 한 줄 가로 스크롤 표시로 돌아간다.
- 결과 목록은 스크롤 기반으로 추가 데이터를 불러오며, 별도의 `더 불러오기` 버튼은 노출하지 않는다.

## Course Detail Modal

- 상세 보기에는 원문 강좌 소개와 별도로 AI 요약을 표시한다.
- AI 추천 태그는 일반 태그와 구분되는 섹션으로 표시한다.
- 강좌 소개가 비어 있으면 AI 요약을 소개 영역의 대체 문구로도 사용한다.

## Default Filters

- 일반 화면의 기본 모집상태 필터는 `OPEN`, `SCHEDULED`, `DEADLINE`, `WAITING`만 선택한다.
- 완전 마감 상태인 `CLOSED`는 기본 목록에서 제외한다.
- `debug` 모드는 검증 목적이므로 모든 모집상태를 기본 포함한다.

## Naver Login

- 네이버 OAuth callback URL은 프론트엔드 루트 기준으로 사용한다.
- 개발 서버 callback URL: `http://localhost:5174/`
- 운영 도메인 callback URL: `https://mooncen.kr/`
- Cloudflare에서 `www.mooncen.kr`만 공개되어 있으면 `https://www.mooncen.kr/`도 네이버 개발자센터 callback URL에 추가해야 한다.
- 프론트엔드는 `VITE_NAVER_OAUTH_CLIENT_ID`가 필요하고, 백엔드는 `NAVER_OAUTH_CLIENT_ID`, `NAVER_OAUTH_CLIENT_SECRET`가 필요하다.
- OAuth provider가 `error` query parameter를 돌려주면 화면에 원인을 표시하도록 처리한다.
- 백엔드 네이버 토큰 교환 요청에는 프론트에서 사용한 `redirect_uri`를 함께 전달한다.
- 배포 스크립트는 nginx `server_name`과 CORS origin에 bare domain과 `www` alias를 함께 반영한다.
## Mobile Logged-In Header

- 로그인 상태에서도 찜, 내강좌, 알림, 사용자 메뉴와 로그아웃 버튼을 모두 표시한다.
- 사용자 이름은 좁은 화면에서 한 글자 배지로 축약해 다른 사용자 메뉴를 밀어내지 않도록 한다.
- 작은 화면, 긴 이름, 찜·알림 건수 표시가 함께 있어도 메뉴가 두 줄로 꺾이거나 화면 밖으로 밀리지 않는지 확인한다.

## Branch Group Expand Stability

- 접힌 지점 그룹의 총개수와 더보기 개수는 현재 검색 조건에서 처음 렌더링된 값을 유지한다.
- 무한 스크롤로 추가 데이터가 로드되어도 접힌 그룹의 버튼 텍스트가 계속 바뀌지 않게 해서 스크롤 중 화면 흔들림을 줄인다.
- 사용자가 지점 그룹을 펼친 뒤에는 현재까지 로드된 강좌 목록을 표시한다.
- 검색어, 지도 범위, 필터 등 조회 조건이 바뀌면 초기 개수 스냅샷을 새로 계산한다.

