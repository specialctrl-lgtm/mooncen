import { describe, expect, it } from 'vitest';
import type { ClassItem } from './data/mockData';
import { courseJsonLd } from './seo';

function offerFor(priceKnown: boolean, price: number) {
  const payload = courseJsonLd({
    id: 'fee-contract',
    title: '요금 계약 강좌',
    provider: 'MUNI_TEST',
    providerLabel: '테스트 기관',
    center: '테스트 센터',
    price,
    priceKnown,
    statusCode: 'OPEN',
  } as ClassItem) as { '@graph': Array<{ offers: Record<string, unknown> }> };
  return payload['@graph'][0].offers;
}

describe('course JSON-LD fee semantics', () => {
  it('does not publish an unknown fee as zero', () => {
    expect(offerFor(false, 0)).not.toHaveProperty('price');
  });

  it('keeps an explicitly free course at zero', () => {
    expect(offerFor(true, 0)).toMatchObject({ price: 0, priceCurrency: 'KRW' });
  });
});
