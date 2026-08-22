interface KakaoMapsLatLng {
  getLat(): number;
  getLng(): number;
}

interface KakaoMapsLatLngBounds {
  extend(latLng: KakaoMapsLatLng): void;
  contain(latLng: KakaoMapsLatLng): boolean;
}

interface KakaoMapsMap {
  addControl(control: KakaoMapsZoomControl, position: number): void;
  getBounds(): KakaoMapsLatLngBounds;
  getCenter(): KakaoMapsLatLng;
  relayout(): void;
  setCenter(latLng: KakaoMapsLatLng): void;
  setBounds(
    bounds: KakaoMapsLatLngBounds,
    paddingTop?: number,
    paddingRight?: number,
    paddingBottom?: number,
    paddingLeft?: number,
  ): void;
  setMaxLevel(level: number): void;
  setMinLevel(level: number): void;
}

interface KakaoMapsMarkerImageOptions {
  offset?: KakaoMapsPoint;
}

interface KakaoMapsMarkerImage {
  readonly __kakaoMarkerImageBrand: never;
}

interface KakaoMapsMarker {
  setImage(image: KakaoMapsMarkerImage): void;
  setMap(map: KakaoMapsMap | null): void;
  setPosition(latLng: KakaoMapsLatLng): void;
  setTitle(title: string): void;
  setZIndex(zIndex: number): void;
}

interface KakaoMapsCircle {
  setMap(map: KakaoMapsMap | null): void;
}

interface KakaoMapsGeocoderResult {
  x: string;
  y: string;
}

interface KakaoMapsGeocoder {
  addressSearch(
    address: string,
    callback: (result: KakaoMapsGeocoderResult[], status: string) => void,
  ): void;
}

interface KakaoMapsSize {
  readonly __kakaoSizeBrand: never;
}

interface KakaoMapsPoint {
  readonly __kakaoPointBrand: never;
}

interface KakaoMapsZoomControl {
  readonly __kakaoZoomControlBrand: never;
}

interface KakaoMapsMapOptions {
  center: KakaoMapsLatLng;
  level?: number;
  draggable?: boolean;
  scrollwheel?: boolean;
  disableDoubleClick?: boolean;
  disableDoubleClickZoom?: boolean;
  keyboardShortcuts?: boolean;
}

interface KakaoMapsMarkerOptions {
  map?: KakaoMapsMap;
  position: KakaoMapsLatLng;
  image?: KakaoMapsMarkerImage;
  title?: string;
  clickable?: boolean;
  zIndex?: number;
}

interface KakaoMapsCircleOptions {
  map?: KakaoMapsMap;
  center: KakaoMapsLatLng;
  radius: number;
  strokeWeight?: number;
  strokeColor?: string;
  strokeOpacity?: number;
  strokeStyle?: string;
  fillColor?: string;
  fillOpacity?: number;
  zIndex?: number;
  clickable?: boolean;
}

interface KakaoMapsNamespace {
  load(callback: () => void): void;
  Map: new (container: HTMLElement, options: KakaoMapsMapOptions) => KakaoMapsMap;
  LatLng: new (latitude: number, longitude: number) => KakaoMapsLatLng;
  LatLngBounds: new () => KakaoMapsLatLngBounds;
  Marker: new (options: KakaoMapsMarkerOptions) => KakaoMapsMarker;
  MarkerImage: new (
    src: string,
    size: KakaoMapsSize,
    options?: KakaoMapsMarkerImageOptions,
  ) => KakaoMapsMarkerImage;
  Circle: new (options: KakaoMapsCircleOptions) => KakaoMapsCircle;
  Size: new (width: number, height: number) => KakaoMapsSize;
  Point: new (x: number, y: number) => KakaoMapsPoint;
  ZoomControl: new () => KakaoMapsZoomControl;
  ControlPosition: {
    RIGHT: number;
  };
  services: {
    Geocoder: new () => KakaoMapsGeocoder;
    Status: {
      OK: string;
    };
  };
  event: {
    addListener(target: KakaoMapsMap | KakaoMapsMarker, type: string, handler: () => void): void;
    removeListener(target: KakaoMapsMap | KakaoMapsMarker, type: string, handler: () => void): void;
  };
}

interface Window {
  kakao?: {
    maps: KakaoMapsNamespace;
  };
  daum?: {
    maps?: KakaoMapsNamespace;
  };
}
