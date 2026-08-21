# Social Login Notes

## 2026-05-28 OAuth-Only Signup Flow

MoonCen uses Google and Naver social login as the account creation path.

- The frontend no longer shows a separate signup modal.
- The login modal only shows Google and Naver login buttons.
- First successful OAuth login still creates the user automatically on the backend.

## 2026-05-28 Naver Display Name Refresh

Naver users could stay displayed as `네이버 사용자` when the account was first created without a usable name.

- The backend now treats provider labels such as `네이버 사용자`, `naver 사용자`, `Google 사용자`, and `사용자` as placeholder names.
- On later OAuth login, if Naver returns `name` or `nickname`, the stored user name is refreshed.
- If Naver only returns an email, the local part of the email is used as a fallback display name.

## 2026-05-28 Header Name Visibility

The frontend header previously hid all `span` elements in the utility nav below `1280px`.

- That rule also hid the logged-in user's display name inside `.user-session-badge`.
- The user session badge now explicitly re-enables the full name below `1280px`.
- On very small mobile screens, the compact initial badge is still used.
- The app also refreshes the stored user with `/api/auth/me` on load so old localStorage names are updated from the backend.
