/**
 * Toast context + hook. Kept separate from Toast.tsx so Fast Refresh still
 * works: react-refresh wants component files to export only components.
 */
import { createContext, useContext } from 'react'

export interface ToastMessage {
  id: string
  title: string
  message?: string
  type: 'info' | 'warning' | 'error' | 'success'
  duration?: number
}

export interface ToastContextValue {
  showToast: (toast: Omit<ToastMessage, 'id'>) => void
}

export const ToastContext = createContext<ToastContextValue | null>(null)

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext)
  if (!ctx) {
    throw new Error('useToast must be used within a ToastProvider')
  }
  return ctx
}
