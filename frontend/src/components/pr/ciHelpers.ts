/**
 * Pure helpers for interpreting GitHub status-check rollups.
 *
 * Kept in a plain module (no component exports) so React Fast Refresh can
 * hot-reload CISection without dropping state.
 */
import type { PRDetail } from '../../types'

type Check = PRDetail['statusCheckRollup'][number]

export function ciResult(check: Check): string {
  return check.conclusion || check.state || check.status || ''
}

export function ciName(check: Check): string {
  return check.name || check.context || ''
}

export function isNonBlocking(check: Check): boolean {
  return ciName(check).indexOf('[Non-Blocking]') >= 0
}

export const FAIL_CONCLUSIONS = [
  'FAILURE', 'CANCELLED', 'TIMED_OUT', 'ACTION_REQUIRED', 'STARTUP_FAILURE',
]

export function isSuccess(result: string): boolean {
  return result === 'SUCCESS' || result === 'NEUTRAL' || result === 'SKIPPED'
}

export function isFailed(result: string): boolean {
  return FAIL_CONCLUSIONS.indexOf(result) >= 0
}
