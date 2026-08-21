# JNE Crawl Notes

## Summary

- `*.jne.go.kr` sites can expose real course lists under `lecture.es`.
- Several collected URLs were marked as blocked because they pointed to `act=view` detail pages without `el_seq`, menu pages, or guide pages.
- For JNE lecture lists, remove the empty `act=view` parameter and crawl the list URL directly.
- `dylib`, `grlib`, `gslib`, `gylib`, and `hnlib.jne.go.kr` share a legacy endpoint that requires the host-scoped TLS 1.2 RSA-GCM compatibility profile in `SafeSession`; certificate-chain and hostname verification stay enabled.

## Confirmed List URLs

| Provider | URL | Result |
|---|---|---|
| `MUNI_DYLIB_JNE_GO_KR_0EC67D8E` | `https://dylib.jne.go.kr/lecture.es?mid=a80402000000` | list confirmed |
| `MUNI_DYLIB_JNE_GO_KR_1412DDEF` | `https://dylib.jne.go.kr/lecture.es?mid=a60402000000` | list confirmed |
| `MUNI_DYLIB_JNE_GO_KR_A2AEEC45` | `https://dylib.jne.go.kr/lecture.es?mid=a60202000000` | list confirmed |
| `MUNI_GRLIB_JNE_GO_KR_133262C9` | `https://grlib.jne.go.kr/lecture.es?mid=a70202010000` | list confirmed |
| `MUNI_GRLIB_JNE_GO_KR_E6838F98` | `https://grlib.jne.go.kr/lecture.es?mid=a70402000000` | list confirmed |
| `MUNI_GSLIB_JNE_GO_KR_80914C01` | `https://gslib.jne.go.kr/lecture.es?mid=c10402000000` | list confirmed |
| `MUNI_GYLIB_JNE_GO_KR_15EB3C2E` | `https://gylib.jne.go.kr/lecture.es?mid=a50402000000` | course-registration list confirmed; scoped TLS compatibility required |
| `MUNI_HNLIB_JNE_GO_KR_3E3E5BCA` | `https://hnlib.jne.go.kr/lecture.es?mid=b30402000000` | list confirmed |
| `MUNI_GYLIFE_JNE_GO_KR_AFB03FB8` | `https://gylife.jne.go.kr/lecture.es?mid=c30402010100` | lifelong learning course list confirmed |

## Remaining JNE Discovery Items

- `MUNI_DYLIB_JNE_GO_KR_A99A023A`: menu page, not a direct course list.
- `MUNI_GSLIB_JNE_GO_KR_F1BD0233`: education guide/detail page, not a direct course list.
