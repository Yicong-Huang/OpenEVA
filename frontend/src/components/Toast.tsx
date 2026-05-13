import { useState, useCallback, useRef } from 'react'
import type { ReactNode } from 'react'
import { ToastContext, type ToastMessage, type ToastContextValue } from './toastContext'

export { useToast, type ToastMessage } from './toastContext'

const TYPE_COLORS: Record<ToastMessage['type'], string> = {
  info: 'var(--blue, #3b82f6)',
  warning: 'var(--yellow, #f59e0b)',
  error: 'var(--red, #ef4444)',
  success: 'var(--green, #22c55e)',
}

function ToastItem({ toast }: { toast: ToastMessage }) {
  return (
    <div
      data-testid="toast-item"
      style={{
        pointerEvents: 'auto',
        background: 'var(--bg-card, #1e1e1e)',
        border: `1px solid ${TYPE_COLORS[toast.type]}`,
        borderLeft: `3px solid ${TYPE_COLORS[toast.type]}`,
        borderRadius: 6,
        padding: '8px 12px',
        minWidth: 220,
        maxWidth: 360,
        boxShadow: '0 4px 12px rgba(0,0,0,0.3)',
      }}
    >
      <div style={{ fontSize: 11, fontWeight: 600, color: TYPE_COLORS[toast.type] }}>
        {toast.title}
      </div>
      {toast.message && (
        <div style={{ fontSize: 10, color: 'var(--text-dim, #999)', marginTop: 2 }}>
          {toast.message}
        </div>
      )}
    </div>
  )
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastMessage[]>([])
  const counterRef = useRef(0)

  const showToast = useCallback((toast: Omit<ToastMessage, 'id'>) => {
    const id = `toast-${++counterRef.current}-${Date.now()}`
    const duration = toast.duration ?? 4000
    const newToast: ToastMessage = { ...toast, id }

    setToasts((prev) => [...prev, newToast])

    if (duration > 0) {
      setTimeout(() => {
        setToasts((prev) => prev.filter((t) => t.id !== id))
      }, duration)
    }
  }, [])

  const value: ToastContextValue = { showToast }

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div
        data-testid="toast-container"
        style={{
          position: 'fixed',
          top: 12,
          right: 12,
          zIndex: 9999,
          display: 'flex',
          flexDirection: 'column',
          gap: 8,
          pointerEvents: 'none',
        }}
      >
        {toasts.map((t) => (
          <ToastItem key={t.id} toast={t} />
        ))}
      </div>
    </ToastContext.Provider>
  )
}
