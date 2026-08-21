import { describe, expect, it } from 'vitest';

import { firstSafeExternalUrl, safeExternalUrl } from './safeUrl';


describe('safeExternalUrl', () => {
  it('accepts only public HTTP(S) URLs', () => {
    expect(safeExternalUrl('https://example.com/course')).toBe('https://example.com/course');
    expect(safeExternalUrl('http://example.com/course')).toBe('http://example.com/course');
    expect(safeExternalUrl('javascript:alert(1)')).toBeNull();
    expect(safeExternalUrl('data:text/html,test')).toBeNull();
    expect(safeExternalUrl('//attacker.example')).toBeNull();
    expect(safeExternalUrl('https://user:password@example.com/private')).toBeNull();
    expect(safeExternalUrl('https://example.com/path\nnext')).toBeNull();
  });

  it('uses the first safe official URL', () => {
    expect(firstSafeExternalUrl(null, 'https://example.com/course/detail'))
      .toBe('https://example.com/course/detail');
    expect(firstSafeExternalUrl('javascript:alert(1)', 'https://example.com/course/detail'))
      .toBe('https://example.com/course/detail');
  });
});
