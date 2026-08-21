import { createContext, useContext, type ReactNode } from 'react';
import type { OpsSession } from './types';

const OpsContext = createContext<OpsSession | null>(null);

export function OpsProvider({ session, children }: { session: OpsSession; children: ReactNode }) {
  return <OpsContext.Provider value={session}>{children}</OpsContext.Provider>;
}

export function useOpsSession(): OpsSession {
  const value = useContext(OpsContext);
  if (!value) throw new Error('Ops session is not available');
  return value;
}
