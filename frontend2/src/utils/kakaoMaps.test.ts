import { afterEach, describe, expect, it } from 'vitest';
import {
  __resetKakaoMapsLoaderForTests,
  kakaoDirectionsLink,
  kakaoMapLink,
  loadKakaoMaps,
} from './kakaoMaps';

afterEach(() => {
  document.getElementById('mooncen-kakao-maps-sdk')?.remove();
  delete window.kakao;
  __resetKakaoMapsLoaderForTests();
});

describe('Kakao Map links', () => {
  it('builds coordinate-based map and directions links', () => {
    const location = {
      name: '롯데문화센터 동탄점',
      address: '경기도 화성시 동탄중앙로 200',
      lat: 37.2,
      lon: 127.1,
    };

    expect(kakaoMapLink(location)).toBe(
      'https://map.kakao.com/link/map/%EB%A1%AF%EB%8D%B0%EB%AC%B8%ED%99%94%EC%84%BC%ED%84%B0%20%EB%8F%99%ED%83%84%EC%A0%90,37.2,127.1',
    );
    expect(kakaoDirectionsLink(location)).toBe(
      'https://map.kakao.com/link/to/%EB%A1%AF%EB%8D%B0%EB%AC%B8%ED%99%94%EC%84%BC%ED%84%B0%20%EB%8F%99%ED%83%84%EC%A0%90,37.2,127.1',
    );
  });

  it('falls back to Kakao Map search when coordinates are unavailable', () => {
    expect(kakaoMapLink({ name: '강좌 장소', address: '서울 중구 세종대로' })).toBe(
      'https://map.kakao.com/link/search/%EC%84%9C%EC%9A%B8%20%EC%A4%91%EA%B5%AC%20%EC%84%B8%EC%A2%85%EB%8C%80%EB%A1%9C',
    );
    expect(kakaoDirectionsLink({ name: '강좌 장소' })).toBe(
      'https://map.kakao.com/link/search/%EA%B0%95%EC%A2%8C%20%EC%9E%A5%EC%86%8C',
    );
  });
});

describe('Kakao Map SDK loader', () => {
  it('shares a single script and promise across map components', async () => {
    const firstLoad = loadKakaoMaps('test-javascript-key');
    const secondLoad = loadKakaoMaps('test-javascript-key');
    const scripts = document.querySelectorAll('#mooncen-kakao-maps-sdk');

    expect(secondLoad).toBe(firstLoad);
    expect(scripts).toHaveLength(1);
    expect((scripts[0] as HTMLScriptElement).src).toContain(
      'dapi.kakao.com/v2/maps/sdk.js?appkey=test-javascript-key&autoload=false',
    );

    const fakeMaps = {
      Map: class FakeMap {},
      load: (callback: () => void) => callback(),
    } as unknown as KakaoMapsNamespace;
    window.kakao = { maps: fakeMaps };
    scripts[0].dispatchEvent(new Event('load'));

    await expect(firstLoad).resolves.toBe(fakeMaps);
    await expect(secondLoad).resolves.toBe(fakeMaps);
  });

  it('removes a failed script so a later call can retry', async () => {
    const failedLoad = loadKakaoMaps('test-javascript-key');
    document.getElementById('mooncen-kakao-maps-sdk')?.dispatchEvent(new Event('error'));

    await expect(failedLoad).rejects.toThrow('스크립트를 불러오지 못했습니다');
    expect(document.getElementById('mooncen-kakao-maps-sdk')).toBeNull();

    const retryLoad = loadKakaoMaps('test-javascript-key');
    expect(document.querySelectorAll('#mooncen-kakao-maps-sdk')).toHaveLength(1);
    document.getElementById('mooncen-kakao-maps-sdk')?.dispatchEvent(new Event('error'));
    await expect(retryLoad).rejects.toThrow('스크립트를 불러오지 못했습니다');
  });
});
