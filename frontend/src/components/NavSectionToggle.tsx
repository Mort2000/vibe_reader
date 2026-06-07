import { ChevronDown, ChevronRight } from 'lucide-react';
import type { ReactNode } from 'react';

export function NavSectionToggle({
  icon,
  label,
  current,
  collapsed,
  onToggle,
}: {
  icon: ReactNode;
  label: string;
  current: string;
  collapsed: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      className="nav-section-toggle"
      type="button"
      aria-expanded={!collapsed}
      onClick={onToggle}
    >
      <span className="nav-section-title">
        {icon}
        <span>{label}</span>
      </span>
      <span className="nav-section-current">{current}</span>
      {collapsed ? <ChevronRight size={17} /> : <ChevronDown size={17} />}
    </button>
  );
}
