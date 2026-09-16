import { createContext, useContext } from 'react';
import type { ProcessKind } from './labTypes';

export interface LabActions {
  updateText: (nodeId: string, text: string) => void;
  generate: (nodeId: string) => void;
  deleteNode: (nodeId: string) => void;
  addProcessNode: (kind: ProcessKind, position: { x: number; y: number }) => void;
}

const LabActionsContext = createContext<LabActions | null>(null);

export const LabActionsProvider = LabActionsContext.Provider;

export function useLabActions(): LabActions {
  const ctx = useContext(LabActionsContext);
  if (!ctx) throw new Error('useLabActions must be used within LabActionsProvider');
  return ctx;
}
