import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { toClassItem } from '../data/mockData';
import type { Course } from '../api';
import { decodeHtmlText, normalizeCourseDisplayTitle } from './titleDisplay';

describe('course title display', () => {
  it('decodes named, decimal, hexadecimal, and double-encoded HTML entities', () => {
    expect(decodeHtmlText('어린이 &amp; 가족 &#183; &#xCCB4;&#xD5D8;')).toBe('어린이 & 가족 · 체험');
    expect(decodeHtmlText('A &amp;amp; B')).toBe('A & B');
  });

  it('decodes the API title while mapping it to a card item', () => {
    const item = toClassItem({
      id: 'entity-title',
      provider: 'PUBLIC',
      title: '어린이 &amp; 가족 체험 &middot; 특별전',
    } as Course);

    expect(item.title).toBe('어린이 & 가족 체험 · 특별전');
  });

  it('keeps decoded markup as escaped React text instead of injecting HTML', () => {
    const title = normalizeCourseDisplayTitle('&lt;img src=x onerror=alert(1)&gt; 안전 체험');
    const { container } = render(<h3>{title}</h3>);

    expect(container.querySelector('img')).toBeNull();
    expect(screen.getByRole('heading').textContent).toBe('<img src=x onerror=alert(1)> 안전 체험');
  });
});
