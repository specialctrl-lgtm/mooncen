# MoonCen Provider Icon System

MoonCen does not use official provider brand logos in branch lists or map popups.
Provider identity is represented by MoonCen-owned shorthand icons.

## Usage

Frontend code should use:

```tsx
<ProviderIcon providerName="홈플러스 문화센터" providerType="culture_center" />
```

Core files:

- `frontend2/src/utils/providerIcon.ts`
- `frontend2/src/components/ProviderIcon.tsx`
- `frontend2/src/styles.css`

## Culture Center Mapping

| Provider | Icon |
|---|---:|
| Homeplus / 홈플러스 | HP |
| Emart / 이마트 | EM |
| Lotte / 롯데 | LT |
| AK Plaza / AK플라자 | AK |
| Hyundai / 현대 | HD |
| Shinsegae / 신세계 | SS |
| 기타 문화센터 | MC |

## Education And Experience Mapping

| Institution Type | Icon |
|---|---:|
| Library / 도서관 | 도 |
| Museum / 박물관 | 박 |
| Science Center / 과학관 | 과 |
| Public Center / 공공기관 | 공 |
| Youth Center / 청소년센터 | 청 |
| Experience / 체험행사 | 체 |
| One Day / 원데이 | 원 |
| 기타 | 기 |

## Design

- Base background: `#CCFBF1`
- Base border: `#14B8A6`
- Base text: `#0F766E`
- List icon: `36px x 36px`
- Small badge: `24px x 24px`
- Popup icon: `44px x 44px`

Brand point colors are used only as subtle icon accents. Map markers remain
MoonCen mint markers where the number means course count.
