import type { ReactNode } from 'react';

import type { LoadStatus } from '../types';

export function StatusPill({
  status,
  children,
}: {
  status: LoadStatus | 'open' | 'connecting' | 'closed' | 'bad' | 'good';
  children: ReactNode;
}) {
  return <span className={`status-pill status-${status}`}>{children}</span>;
}
