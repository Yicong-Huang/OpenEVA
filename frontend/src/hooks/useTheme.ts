/**
 * Theme + personalization hook.
 *
 * Beyond the legacy dark/light toggle, this hook now drives:
 *   - palette: dark | light | solarized-dark | solarized-light |
 *              high-contrast | nord | rose-pine-moon | github-dimmed |
 *              cobalt | tokyo-night | catppuccin-mocha | gruvbox-dark |
 *              everforest-dark | dracula | crimson-dark | slate-pro
 *   - fontScale: 0.85 / 1.0 / 1.15 / 1.3  (compact -> large)
 *   - density:   compact | normal | spacious  (margin/padding multiplier)
 *   - fontFamily: system | sans | serif | mono | rounded
 *   - brightness: 0.85 / 0.95 / 1.0 / 1.05 / 1.15  (CSS filter on body)
 *
 * Storage: localStorage key per dimension so a long-time dark-mode
 * user upgrading the app still lands on dark, not whatever default
 * the new code ships with.
 *
 * The page applies each by writing data-theme + CSS variables on
 * `:root`. CSS owns the actual visuals; this module is purely a
 * preferences-state container.
 */
import { useState, useEffect, useCallback } from 'react'

export type Theme =
  | 'dark' | 'light'
  | 'solarized-dark' | 'solarized-light'
  | 'high-contrast' | 'nord'
  | 'rose-pine-moon' | 'github-dimmed' | 'cobalt'
  | 'tokyo-night' | 'catppuccin-mocha'
  | 'gruvbox-dark' | 'everforest-dark' | 'dracula'
  | 'crimson-dark' | 'slate-pro'
  | 'midnight-violet' | 'paper-warm'

export const THEMES: Theme[] = [
  'dark', 'light', 'solarized-dark', 'solarized-light',
  'high-contrast', 'nord',
  'rose-pine-moon', 'github-dimmed', 'cobalt',
  'tokyo-night', 'catppuccin-mocha',
  'gruvbox-dark', 'everforest-dark', 'dracula',
  'crimson-dark', 'slate-pro',
  'midnight-violet', 'paper-warm',
]

export type FontScale = 0.85 | 1 | 1.15 | 1.3
export const FONT_SCALES: FontScale[] = [0.85, 1, 1.15, 1.3]

export type Density = 'compact' | 'normal' | 'spacious'
export const DENSITIES: Density[] = ['compact', 'normal', 'spacious']

// Font family is independent of palette so any of the 14 themes can be
// paired with any of these stacks. `system` matches the historical
// default (the OS UI font); the others are common alternatives shipped
// with macOS / Windows / common Linux distros so the user gets the
// chosen face without installing anything.
export type FontFamily = 'system' | 'sans' | 'serif' | 'mono' | 'rounded'
export const FONT_FAMILIES: FontFamily[] = [
  'system', 'sans', 'serif', 'mono', 'rounded',
]
export const FONT_FAMILY_STACKS: Record<FontFamily, string> = {
  system: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
  sans: 'Inter, "Helvetica Neue", Helvetica, Arial, system-ui, sans-serif',
  serif: 'Georgia, "Times New Roman", "Liberation Serif", serif',
  mono: 'Menlo, Monaco, "Cascadia Mono", "Courier New", monospace',
  rounded: '"SF Pro Rounded", "Avenir Next", "Quicksand", system-ui, sans-serif',
}

// Brightness is a CSS `filter: brightness()` multiplier applied to the
// body. Decoupled from theme palette so the user can keep a dark theme
// but make the whole UI a touch brighter / dimmer (eyestrain
// preference, ambient lighting). 1.0 is the baseline; range was tuned
// to stay readable on every shipped palette.
export type Brightness = 0.85 | 0.95 | 1 | 1.05 | 1.15
export const BRIGHTNESSES: Brightness[] = [0.85, 0.95, 1, 1.05, 1.15]

const STORAGE_KEY = 'eva-theme'
const FONT_KEY = 'eva-font-scale'
const DENSITY_KEY = 'eva-density'
const FONT_FAMILY_KEY = 'eva-font-family'
const BRIGHTNESS_KEY = 'eva-brightness'


function isTheme(v: unknown): v is Theme {
  return typeof v === 'string' && (THEMES as string[]).includes(v)
}

function isDensity(v: unknown): v is Density {
  return typeof v === 'string' && (DENSITIES as string[]).includes(v)
}

function isFontFamily(v: unknown): v is FontFamily {
  return typeof v === 'string' && (FONT_FAMILIES as string[]).includes(v)
}

function getInitialTheme(): Theme {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (isTheme(stored)) return stored
  } catch { /* ignore */ }
  return 'dark'
}

function getInitialFontScale(): FontScale {
  try {
    const stored = localStorage.getItem(FONT_KEY)
    if (stored) {
      const n = Number(stored)
      if ((FONT_SCALES as number[]).includes(n)) return n as FontScale
    }
  } catch { /* ignore */ }
  return 1
}

function getInitialDensity(): Density {
  try {
    const stored = localStorage.getItem(DENSITY_KEY)
    if (isDensity(stored)) return stored
  } catch { /* ignore */ }
  return 'normal'
}

function getInitialFontFamily(): FontFamily {
  try {
    const stored = localStorage.getItem(FONT_FAMILY_KEY)
    if (isFontFamily(stored)) return stored
  } catch { /* ignore */ }
  return 'system'
}

function getInitialBrightness(): Brightness {
  try {
    const stored = localStorage.getItem(BRIGHTNESS_KEY)
    if (stored) {
      const n = Number(stored)
      if ((BRIGHTNESSES as number[]).includes(n)) return n as Brightness
    }
  } catch { /* ignore */ }
  return 1
}

function applyTheme(theme: Theme) {
  document.documentElement.setAttribute('data-theme', theme)
}

function applyFontScale(scale: FontScale) {
  document.documentElement.style.setProperty('--font-scale', String(scale))
}

const DENSITY_GAP: Record<Density, string> = {
  compact: '0.7',
  normal: '1',
  spacious: '1.4',
}

function applyDensity(d: Density) {
  document.documentElement.style.setProperty('--gap', DENSITY_GAP[d])
  document.documentElement.setAttribute('data-density', d)
}

function applyFontFamily(f: FontFamily) {
  document.documentElement.style.setProperty(
    '--font-family', FONT_FAMILY_STACKS[f],
  )
  document.documentElement.setAttribute('data-font-family', f)
}

function applyBrightness(b: Brightness) {
  // Use a CSS variable (consumed by `body { filter: brightness(var(...)) }`)
  // instead of writing the filter directly so themes that disable the
  // global filter (high-contrast, future) can opt-out via CSS.
  document.documentElement.style.setProperty('--brightness', String(b))
}


export function useTheme() {
  const [theme, setThemeState] = useState<Theme>(getInitialTheme)
  const [fontScale, setFontScaleState] = useState<FontScale>(getInitialFontScale)
  const [density, setDensityState] = useState<Density>(getInitialDensity)
  const [fontFamily, setFontFamilyState] = useState<FontFamily>(getInitialFontFamily)
  const [brightness, setBrightnessState] = useState<Brightness>(getInitialBrightness)

  // Apply each dimension on mount + subsequent changes.
  useEffect(() => { applyTheme(theme) }, [theme])
  useEffect(() => { applyFontScale(fontScale) }, [fontScale])
  useEffect(() => { applyDensity(density) }, [density])
  useEffect(() => { applyFontFamily(fontFamily) }, [fontFamily])
  useEffect(() => { applyBrightness(brightness) }, [brightness])

  const setTheme = useCallback((t: Theme) => {
    setThemeState(t)
    try { localStorage.setItem(STORAGE_KEY, t) } catch { /* ignore */ }
  }, [])

  // Backwards-compatible toggle: cycles dark <-> light only. Other
  // palettes need to be picked explicitly via Settings.
  const toggle = useCallback(() => {
    setTheme(theme === 'dark' ? 'light' : theme === 'light' ? 'dark' : 'dark')
  }, [theme, setTheme])

  const setFontScale = useCallback((s: FontScale) => {
    setFontScaleState(s)
    try { localStorage.setItem(FONT_KEY, String(s)) } catch { /* ignore */ }
  }, [])

  const setDensity = useCallback((d: Density) => {
    setDensityState(d)
    try { localStorage.setItem(DENSITY_KEY, d) } catch { /* ignore */ }
  }, [])

  const setFontFamily = useCallback((f: FontFamily) => {
    setFontFamilyState(f)
    try { localStorage.setItem(FONT_FAMILY_KEY, f) } catch { /* ignore */ }
  }, [])

  const setBrightness = useCallback((b: Brightness) => {
    setBrightnessState(b)
    try { localStorage.setItem(BRIGHTNESS_KEY, String(b)) } catch { /* ignore */ }
  }, [])

  return {
    theme, setTheme, toggle,
    fontScale, setFontScale,
    density, setDensity,
    fontFamily, setFontFamily,
    brightness, setBrightness,
  }
}
