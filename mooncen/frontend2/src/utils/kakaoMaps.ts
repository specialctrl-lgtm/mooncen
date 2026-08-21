const KAKAO_MAPS_SCRIPT_ID = 'mooncen-kakao-maps-sdk';
const KAKAO_MAPS_LOAD_TIMEOUT_MS = 15_000;

let loaderPromise: Promise<KakaoMapsNamespace> | null = null;
let loaderKey: string | null = null;

function finishSdkLoad(resolve: (maps: KakaoMapsNamespace) => void, reject: (error: Error) => void) {
  const maps = window.kakao?.maps;
  if (!maps?.load) {
    reject(new Error('카카오 지도 SDK를 초기화하지 못했습니다.'));
    return;
  }

  maps.load(() => {
    const loadedMaps = window.kakao?.maps;
    if (loadedMaps?.Map) {
      resolve(loadedMaps);
      return;
    }
    reject(new Error('카카오 지도 SDK가 완전히 로드되지 않았습니다.'));
  });
}

export function loadKakaoMaps(javascriptKey: string): Promise<KakaoMapsNamespace> {
  const normalizedKey = javascriptKey.trim();
  if (!normalizedKey) return Promise.reject(new Error('카카오 지도 JavaScript 키가 없습니다.'));
  if (typeof window === 'undefined' || typeof document === 'undefined') {
    return Promise.reject(new Error('카카오 지도는 브라우저에서만 사용할 수 있습니다.'));
  }

  if (loaderPromise) {
    if (loaderKey !== normalizedKey) {
      return Promise.reject(new Error('서로 다른 카카오 지도 JavaScript 키를 동시에 사용할 수 없습니다.'));
    }
    return loaderPromise;
  }

  loaderKey = normalizedKey;
  loaderPromise = new Promise<KakaoMapsNamespace>((resolve, reject) => {
    let settled = false;
    const settle = (callback: () => void) => {
      if (settled) return;
      settled = true;
      window.clearTimeout(timeoutId);
      callback();
    };
    const succeed = () => finishSdkLoad(
      (maps) => settle(() => resolve(maps)),
      (error) => settle(() => reject(error)),
    );
    const fail = () => settle(() => reject(new Error('카카오 지도 SDK 스크립트를 불러오지 못했습니다.')));
    const timeoutId = window.setTimeout(
      () => settle(() => reject(new Error('카카오 지도 SDK 로딩 시간이 초과되었습니다.'))),
      KAKAO_MAPS_LOAD_TIMEOUT_MS,
    );

    const existingScript = document.getElementById(KAKAO_MAPS_SCRIPT_ID) as HTMLScriptElement | null;
    if (window.kakao?.maps?.load) {
      succeed();
      return;
    }
    if (existingScript) {
      existingScript.addEventListener('load', succeed, { once: true });
      existingScript.addEventListener('error', fail, { once: true });
      return;
    }

    const script = document.createElement('script');
    script.id = KAKAO_MAPS_SCRIPT_ID;
    script.async = true;
    script.src = `https://dapi.kakao.com/v2/maps/sdk.js?appkey=${encodeURIComponent(normalizedKey)}&autoload=false&libraries=services`;
    script.addEventListener('load', succeed, { once: true });
    script.addEventListener('error', fail, { once: true });
    document.head.appendChild(script);
  }).catch((error: unknown) => {
    document.getElementById(KAKAO_MAPS_SCRIPT_ID)?.remove();
    document
      .querySelectorAll<HTMLScriptElement>('script[src*="t1.daumcdn.net/mapjsapi/"]')
      .forEach((script) => script.remove());
    delete window.kakao;
    if (window.daum) delete window.daum.maps;
    loaderPromise = null;
    loaderKey = null;
    throw error;
  });

  return loaderPromise;
}

function finiteCoordinate(value: number | null | undefined): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function coordinateLink(kind: 'map' | 'to', name: string, lat: number, lon: number) {
  return `https://map.kakao.com/link/${kind}/${encodeURIComponent(name)},${lat},${lon}`;
}

export function kakaoMapLink({
  name,
  address,
  lat,
  lon,
}: {
  name: string;
  address?: string | null;
  lat?: number | null;
  lon?: number | null;
}) {
  if (finiteCoordinate(lat) && finiteCoordinate(lon)) return coordinateLink('map', name, lat, lon);
  return `https://map.kakao.com/link/search/${encodeURIComponent(address || name)}`;
}

export function kakaoDirectionsLink({
  name,
  address,
  lat,
  lon,
}: {
  name: string;
  address?: string | null;
  lat?: number | null;
  lon?: number | null;
}) {
  if (finiteCoordinate(lat) && finiteCoordinate(lon)) return coordinateLink('to', name, lat, lon);
  return `https://map.kakao.com/link/search/${encodeURIComponent(address || name)}`;
}

export function __resetKakaoMapsLoaderForTests() {
  loaderPromise = null;
  loaderKey = null;
}
