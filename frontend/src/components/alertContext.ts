/**
 * Alert context + hook. Split from Alert.tsx so Fast Refresh keeps working
 * (react-refresh wants component files to export components only).
 *
 * Replaces browser-native window.confirm / window.alert / window.prompt with
 * an async API that returns a Promise -- drops in where the sync calls were.
 */
import { createContext, useContext } from 'react'

export type AlertKind = 'info' | 'warning' | 'error' | 'success'

export interface ConfirmOptions {
  title: string
  message?: string
  confirmLabel?: string   // default: 'Confirm'
  cancelLabel?: string    // default: 'Cancel'
  danger?: boolean        // red confirm button (for destructive actions)
  kind?: AlertKind        // tint the icon/accent
}

export interface AlertOptions {
  title: string
  message?: string
  okLabel?: string        // default: 'OK'
  kind?: AlertKind        // default: 'info'
}

export interface PromptOptions {
  title: string
  message?: string
  defaultValue?: string
  placeholder?: string
  confirmLabel?: string
  cancelLabel?: string
  multiline?: boolean
}

// Configures an extra checkbox alongside the prompt's text input.
// Used by flows where the user picks a *value* AND a yes/no choice in
// one dialog -- e.g. "close task" with an optional reason + a
// `Close linked ticket` toggle.
export interface PromptCheckboxOption {
  label: string
  defaultChecked?: boolean
}

export interface PromptCheckboxResult {
  value: string
  checked: boolean
}

// Anchor point for non-blocking popover confirms. `confirmAt` floats a
// small bubble near the user's pointer instead of opening a centred
// modal -- chosen for low-stakes destructive actions (Unpin, Remove
// dependency) where a full backdrop dialog feels heavy.
export interface PopConfirmAnchor {
  x: number
  y: number
}

export interface AlertContextValue {
  confirm: (opts: ConfirmOptions) => Promise<boolean>
  confirmAt: (opts: ConfirmOptions, anchor: PopConfirmAnchor) => Promise<boolean>
  alert: (opts: AlertOptions) => Promise<void>
  prompt: (opts: PromptOptions) => Promise<string | null>
  promptWithCheckbox: (
    opts: PromptOptions & { checkbox: PromptCheckboxOption },
  ) => Promise<PromptCheckboxResult | null>
}

export const AlertContext = createContext<AlertContextValue | null>(null)

// Fallback used when no provider is mounted (test envs / library consumers).
// Routes to the native browser dialogs so existing mocks of window.confirm /
// window.alert / window.prompt keep working without code changes.
const _nativeFallback: AlertContextValue = {
  confirm: (opts) => Promise.resolve(window.confirm(
    opts.message ? `${opts.title}\n${opts.message}` : opts.title,
  )),
  // Falls back to plain confirm -- the native browser dialog has no
  // pointer-anchored variant, so we just behave like blocking confirm
  // when no provider is mounted (test env / standalone usage).
  confirmAt: (opts) => Promise.resolve(window.confirm(
    opts.message ? `${opts.title}\n${opts.message}` : opts.title,
  )),
  alert: (opts) => {
    window.alert(opts.message ? `${opts.title}\n${opts.message}` : opts.title)
    return Promise.resolve()
  },
  prompt: (opts) => Promise.resolve(window.prompt(
    opts.message ? `${opts.title}\n${opts.message}` : opts.title,
    opts.defaultValue ?? '',
  )),
  // Native browser prompt has no checkbox; fall back to the prompt
  // text + assume the checkbox sits at its default.
  promptWithCheckbox: (opts) => {
    const v = window.prompt(
      opts.message ? `${opts.title}\n${opts.message}` : opts.title,
      opts.defaultValue ?? '',
    )
    if (v === null) return Promise.resolve(null)
    return Promise.resolve({ value: v, checked: !!opts.checkbox.defaultChecked })
  },
}

export function useAlert(): AlertContextValue {
  return useContext(AlertContext) ?? _nativeFallback
}
