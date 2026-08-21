import { useEffect, useMemo, useState, type CSSProperties } from 'react';
import { getProviderIcon, type ProviderIconSize } from '../utils/providerIcon';

type ProviderIconProps = {
  providerName?: string | null;
  providerType?: string | null;
  centerName?: string | null;
  websiteUrl?: string | null;
  faviconUrl?: string | null;
  size?: ProviderIconSize;
  active?: boolean;
  disabled?: boolean;
  className?: string;
};

export default function ProviderIcon({
  providerName,
  providerType,
  centerName,
  websiteUrl,
  faviconUrl,
  size = 'medium',
  active = false,
  disabled = false,
  className,
}: ProviderIconProps) {
  const icon = getProviderIcon(providerName, providerType, centerName);
  const imageSrc = useMemo(() => {
    if (icon.group === 'culture') return '';
    if (faviconUrl) return faviconUrl;
    if (!websiteUrl) return '';
    try {
      const url = new URL(websiteUrl);
      return `${url.origin}/favicon.ico`;
    } catch {
      return '';
    }
  }, [faviconUrl, icon.group, websiteUrl]);
  const [imageFailed, setImageFailed] = useState(false);
  useEffect(() => setImageFailed(false), [imageSrc]);
  const isLongLabel = icon.label.length >= 4;

  const classes = [
    'provider-icon',
    `provider-icon-${size}`,
    `provider-icon-${icon.group}`,
    `provider-icon-${icon.key}`,
    isLongLabel ? 'provider-icon-long-label' : '',
    imageSrc && !imageFailed ? 'provider-icon-image' : '',
    active ? 'active' : '',
    disabled ? 'disabled' : '',
    className || '',
  ].filter(Boolean).join(' ');

  return (
    <span
      className={classes}
      style={{ '--provider-icon-accent': icon.accent } as CSSProperties}
      title={icon.title}
      aria-label={icon.title}
      role="img"
    >
      {imageSrc && !imageFailed ? (
        <img src={imageSrc} alt="" loading="lazy" referrerPolicy="no-referrer" onError={() => setImageFailed(true)} />
      ) : (
        <span className="provider-icon-label">{icon.label}</span>
      )}
    </span>
  );
}
