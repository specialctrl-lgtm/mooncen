import type { CSSProperties } from 'react';
import { getCategoryIcon, type CategoryIconGroup } from '../utils/categoryIcon';

type CategoryIconProps = {
  label: string;
  group?: CategoryIconGroup;
  active?: boolean;
  size?: 'small' | 'medium' | 'large';
};

export default function CategoryIcon({ label, group = 'culture', active = false, size = 'medium' }: CategoryIconProps) {
  const icon = getCategoryIcon(label, group);
  const classes = [
    'category-icon',
    `category-icon-${size}`,
    active ? 'active' : '',
  ].filter(Boolean).join(' ');

  return (
    <span
      className={classes}
      style={{
        '--category-icon-color': icon.color,
        '--category-icon-bg': icon.background,
      } as CSSProperties}
      aria-hidden="true"
    >
      <svg viewBox="0 0 48 48" focusable="false">
        <g
          fill="none"
          stroke="currentColor"
          strokeWidth="2.8"
          strokeLinecap="round"
          strokeLinejoin="round"
          dangerouslySetInnerHTML={{ __html: icon.path }}
        />
      </svg>
    </span>
  );
}
