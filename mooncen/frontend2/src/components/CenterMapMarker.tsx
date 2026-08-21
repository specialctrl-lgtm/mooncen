import { useEffect, useMemo, useRef, useState } from 'react';
import type { Branch } from '../api';
import { branchCoordinates } from '../utils/branchCoordinates';

type CenterMapMarkerProps = {
  map: KakaoMapsMap;
  maps: KakaoMapsNamespace;
  branch: Branch;
  courseCount: number;
  selected: boolean;
  favorite: boolean;
  urgent: boolean;
  inactive?: boolean;
  highlighted?: boolean;
  onClick: () => void;
  onHover?: (branchId: string | null) => void;
};

type MarkerPalette = {
  border: string;
  pin: string;
  text: string;
};

type MarkerModel = {
  key: string;
  label?: string;
  iconPath?: string;
  palette: MarkerPalette;
};

const cultureProviderCodes = new Set([
  'HOMEPLUS',
  'LOTTE',
  'LOTTE_MART',
  'EMART',
  'HYUNDAI_DEPT',
  'GALLERIA',
  'AK_PLAZA',
  'ELAND_RETAIL',
  'SHINSEGAE_ACADEMY',
]);

const palettes: Record<string, MarkerPalette> = {
  homeplus: { border: '#E11D2E', pin: '#E11D2E', text: '#E11D2E' },
  emart: { border: '#F59E0B', pin: '#F59E0B', text: '#374151' },
  lotte: { border: '#FB4055', pin: '#FB4055', text: '#E11D2E' },
  ak: { border: '#1D5FAE', pin: '#1D5FAE', text: '#1D5FAE' },
  hyundai: { border: '#047857', pin: '#047857', text: '#166534' },
  shinsegae: { border: '#0F9F95', pin: '#0F9F95', text: '#0F766E' },
  galleria: { border: '#7C3AED', pin: '#7C3AED', text: '#6D28D9' },
  library: { border: '#28B9B0', pin: '#42C6BF', text: '#179C93' },
  museum: { border: '#F59E0B', pin: '#F59E0B', text: '#EA580C' },
  science: { border: '#1D9BF0', pin: '#38A9E8', text: '#1D4ED8' },
  public: { border: '#4CAF3D', pin: '#63B64E', text: '#2F8E28' },
  youth: { border: '#8B42C7', pin: '#9350C8', text: '#7C3AED' },
  other: { border: '#2EADE1', pin: '#35B6E8', text: '#1D9BF0' },
};

const iconPaths: Record<string, string> = {
  library: '<path d="M18 20c6 0 9 2 10 5v18c-2-3-5-4-10-4V20Z"/><path d="M28 25c1-3 4-5 10-5v19c-5 0-8 1-10 4V25Z"/>',
  museum: '<path d="M18 25h20M21 25v15M28 25v15M35 25v15M17 40h22M19 21l9-6 9 6H19Z"/>',
  science: '<path d="M25 16v12l-8 15h22l-8-15V16"/><path d="M23 16h10M22 35h12"/><circle cx="29" cy="33" r="2"/>',
  public: '<path d="M18 25h20M21 25v15M28 25v15M35 25v15M17 40h22M20 21l8-6 8 6H20Z"/><path d="M28 15v-5h7"/>',
  youth: '<circle cx="23" cy="24" r="5"/><circle cx="35" cy="24" r="5"/><path d="M15 41c2-7 6-10 13-10M28 31c7 0 11 3 13 10"/>',
  other: '<path d="m28 16 4 8 9 1.3-6.5 6.3 1.5 9-8-4.2-8 4.2 1.5-9-6.5-6.3 9-1.3 4-8Z"/>',
};

function useCompactMarker() {
  const [compact, setCompact] = useState(false);

  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return undefined;
    const media = window.matchMedia('(max-width: 640px)');
    const sync = () => setCompact(media.matches);
    sync();
    media.addEventListener('change', sync);
    return () => media.removeEventListener('change', sync);
  }, []);

  return compact;
}

function sourceText(branch: Branch) {
  return [
    branch.provider,
    branch.provider_label,
    branch.name,
    branch.address,
    branch.facility_type,
    branch.facility_category,
    branch.facility_service_group,
    branch.facility_collection_category,
    branch.primary_service_group,
    ...(branch.service_groups || []),
    branch.primary_collection_category,
    ...(branch.collection_categories || []),
  ]
    .filter(Boolean)
    .join(' ')
    .toLowerCase();
}

function containsAny(source: string, keywords: string[]) {
  return keywords.some((keyword) => source.includes(keyword.toLowerCase()));
}

function isCultureBranch(branch: Branch) {
  if (branch.provider === 'CULTURE_FACILITY') return false;
  const source = sourceText(branch);
  return cultureProviderCodes.has(branch.provider) || containsAny(source, ['문화센터', '문화아카데미', '아카데미']);
}

function isFacilityInfoBranch(branch: Branch) {
  return branch.provider === 'CULTURE_FACILITY' || Boolean(branch.facility_source);
}

function cultureMarkerModel(branch: Branch): MarkerModel {
  switch (branch.provider) {
    case 'HOMEPLUS':
      return { key: 'homeplus', label: 'HP', palette: palettes.homeplus };
    case 'EMART':
      return { key: 'emart', label: 'E', palette: palettes.emart };
    case 'LOTTE':
    case 'LOTTE_MART':
      return { key: 'lotte', label: '롯데', palette: palettes.lotte };
    case 'AK_PLAZA':
      return { key: 'ak', label: 'AK', palette: palettes.ak };
    case 'HYUNDAI_DEPT':
      return { key: 'hyundai', label: 'HD', palette: palettes.hyundai };
    case 'SHINSEGAE_ACADEMY':
      return { key: 'shinsegae', label: 'SS', palette: palettes.shinsegae };
    case 'GALLERIA':
      return { key: 'galleria', label: '갤러', palette: palettes.galleria };
    default:
      return { key: 'other', iconPath: iconPaths.other, palette: palettes.other };
  }
}

function institutionMarkerModel(branch: Branch): MarkerModel {
  const source = sourceText(branch);

  if (containsAny(source, ['도서관', 'library', 'lib_'])) {
    return { key: 'library', iconPath: iconPaths.library, palette: palettes.library };
  }
  if (containsAny(source, ['박물관', '미술관', 'museum'])) {
    return { key: 'museum', iconPath: iconPaths.museum, palette: palettes.museum };
  }
  if (containsAny(source, ['과학관', '과학', 'science'])) {
    return { key: 'science', iconPath: iconPaths.science, palette: palettes.science };
  }
  if (containsAny(source, ['청소년', '청년', 'youth'])) {
    return { key: 'youth', iconPath: iconPaths.youth, palette: palettes.youth };
  }
  if (containsAny(source, ['시청', '구청', '군청', '공공', '평생', '시설', 'go.kr'])) {
    return { key: 'public', iconPath: iconPaths.public, palette: palettes.public };
  }

  return { key: 'other', iconPath: iconPaths.other, palette: palettes.other };
}

function markerModel(branch: Branch): MarkerModel {
  return isCultureBranch(branch) ? cultureMarkerModel(branch) : institutionMarkerModel(branch);
}

function markerContent(model: MarkerModel, palette: MarkerPalette, favorite: boolean) {
  if (favorite) {
    return '<path d="M28 39.5s-11.5-6.8-11.5-15c0-4.4 3.2-7.5 7.3-7.5 2.3 0 4.2 1.1 5.2 2.8 1-1.7 2.9-2.8 5.2-2.8 4.1 0 7.3 3.1 7.3 7.5 0 8.2-11.5 15-11.5 15Z" fill="#F43F5E"/>';
  }

  if (model.iconPath) {
    return `<g fill="none" stroke="${palette.text}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round">${model.iconPath}</g>`;
  }

  const label = model.label || '';
  const fontSize = label.length > 1 ? 14 : 18;
  return `<text x="28" y="34" text-anchor="middle" font-family="Arial, sans-serif" font-size="${fontSize}" font-weight="900" fill="${palette.text}">${label}</text>`;
}

function markerSvgUrl(model: MarkerModel, active: boolean, favorite: boolean, urgent: boolean, inactive: boolean) {
  const palette = inactive
    ? { border: '#CBD5E1', pin: '#94A3B8', text: '#64748B' }
    : model.palette;
  const border = active ? palette.text : palette.border;
  const strokeWidth = active ? 4 : 3;
  const urgentBadge = urgent
    ? `<circle cx="46" cy="47" r="5" fill="#EF4444" stroke="#FFFFFF" stroke-width="2"/>`
    : '';
  const activeTransform = active ? 'translate(0 -1)' : '';

  const svg = `
    <svg xmlns="http://www.w3.org/2000/svg" width="56" height="66" viewBox="0 0 56 66">
      <ellipse cx="28" cy="61" rx="12" ry="4" fill="#0F172A" opacity="${active ? '0.22' : '0.12'}"/>
      <g transform="${activeTransform}">
        <path d="M28 64 18 49C11 45 7 38 7 30 7 16 16.5 6.5 28 6.5S49 16 49 30c0 8-4 15-11 19L28 64Z" fill="${palette.pin}"/>
        <circle cx="28" cy="29" r="21" fill="#FFFFFF" stroke="${border}" stroke-width="${strokeWidth}"/>
        ${markerContent(model, palette, favorite)}
        ${urgentBadge}
      </g>
    </svg>`;

  return `data:image/svg+xml;charset=UTF-8,${encodeURIComponent(svg)}`;
}

function makeMarkerImage(
  maps: KakaoMapsNamespace,
  url: string,
  active: boolean,
  compact: boolean,
): KakaoMapsMarkerImage {
  const width = compact ? (active ? 38 : 34) : (active ? 44 : 40);
  const height = compact ? (active ? 45 : 41) : (active ? 52 : 48);

  return new maps.MarkerImage(
    url,
    new maps.Size(width, height),
    { offset: new maps.Point(width / 2, height) },
  );
}

export default function CenterMapMarker({
  map,
  maps,
  branch,
  courseCount,
  selected,
  favorite,
  urgent,
  inactive = false,
  highlighted = false,
  onClick,
  onHover,
}: CenterMapMarkerProps) {
  const [hovered, setHovered] = useState(false);
  const markerRef = useRef<KakaoMapsMarker | null>(null);
  const onClickRef = useRef(onClick);
  const onHoverRef = useRef(onHover);
  const compact = useCompactMarker();
  const active = selected || highlighted || hovered;
  const model = useMemo(() => markerModel(branch), [branch]);
  const iconUrl = useMemo(() => markerSvgUrl(model, active, favorite, urgent, inactive), [active, favorite, inactive, model, urgent]);
  const icon = useMemo(
    () => makeMarkerImage(maps, iconUrl, active, compact),
    [active, compact, iconUrl, maps],
  );
  const title = isFacilityInfoBranch(branch) && courseCount <= 0
    ? `${branch.name} 체험 시설`
    : `${branch.name} ${courseCount.toLocaleString('ko-KR')}개 강좌`;
  const zIndex = selected ? 1000 : hovered || highlighted ? 900 : urgent ? 650 : favorite ? 600 : 200;

  useEffect(() => {
    onClickRef.current = onClick;
    onHoverRef.current = onHover;
  }, [onClick, onHover]);

  useEffect(() => {
    const coordinates = branchCoordinates(branch);
    if (!coordinates) return undefined;
    const nextMarker = new maps.Marker({
      map,
      position: new maps.LatLng(coordinates.lat, coordinates.lon),
      image: icon,
      title,
      clickable: true,
      zIndex,
    });
    const handleClick = () => onClickRef.current();
    const handleMouseOver = () => {
      setHovered(true);
      onHoverRef.current?.(branch.id);
    };
    const handleMouseOut = () => {
      setHovered(false);
      onHoverRef.current?.(null);
    };

    maps.event.addListener(nextMarker, 'click', handleClick);
    maps.event.addListener(nextMarker, 'mouseover', handleMouseOver);
    maps.event.addListener(nextMarker, 'mouseout', handleMouseOut);
    markerRef.current = nextMarker;

    return () => {
      maps.event.removeListener(nextMarker, 'click', handleClick);
      maps.event.removeListener(nextMarker, 'mouseover', handleMouseOver);
      maps.event.removeListener(nextMarker, 'mouseout', handleMouseOut);
      nextMarker.setMap(null);
      if (markerRef.current === nextMarker) markerRef.current = null;
    };
  // The icon and z-index are updated in the effect below without recreating the marker.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [branch.id, branch.lat, branch.lon, map, maps, title]);

  useEffect(() => {
    markerRef.current?.setImage(icon);
    markerRef.current?.setZIndex(zIndex);
  }, [icon, zIndex]);

  return null;
}
