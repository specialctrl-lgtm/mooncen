import { Building2, GraduationCap, Landmark, type LucideIcon } from 'lucide-react';

type ScopeIconProps = {
  type: 'culture' | 'education' | 'experience';
  active?: boolean;
};

const scopeIcons: Record<ScopeIconProps['type'], LucideIcon> = {
  culture: Building2,
  education: GraduationCap,
  experience: Landmark,
};

export default function ScopeIcon({ type, active = false }: ScopeIconProps) {
  const Icon = scopeIcons[type];
  return (
    <span className={`scope-icon scope-icon-${type} ${active ? 'active' : ''}`} aria-hidden="true">
      <Icon size={25} strokeWidth={1.8} />
    </span>
  );
}
