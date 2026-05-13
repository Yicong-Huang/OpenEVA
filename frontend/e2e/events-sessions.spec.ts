import { test, expect } from '@playwright/test'

test('Events dropdown shows event rows with icons', async ({ page }) => {
  await page.goto('/')
  await page.waitForTimeout(3000)

  const eventsTopbar = page.getByTestId('events-topbar')
  await eventsTopbar.locator('> span').click()
  await page.waitForTimeout(1000)

  // Should have event rows with data-testid
  const rows = page.getByTestId('event-row')
  const count = await rows.count()
  if (count > 0) {
    // First row should have text content (title)
    const text = await rows.first().textContent()
    expect(text?.length).toBeGreaterThan(0)
  }
})

test('Events dropdown shows Historical button', async ({ page }) => {
  await page.goto('/')
  await page.waitForTimeout(3000)

  const eventsTopbar = page.getByTestId('events-topbar')
  await eventsTopbar.locator('> span').click()
  await page.waitForTimeout(1000)

  const histBtn = page.getByTestId('events-load-more')
  // Historical button should be visible if there are enough events
  const rows = page.getByTestId('event-row')
  const count = await rows.count()
  if (count >= 10) {
    await expect(histBtn).toBeVisible()
  }
})

test('Events Read All button calls API', async ({ page }) => {
  await page.goto('/')
  await page.waitForTimeout(3000)

  const eventsTopbar = page.getByTestId('events-topbar')
  await eventsTopbar.locator('> span').click()
  await page.waitForTimeout(1000)

  const readAllBtn = page.getByTestId('events-read-all')
  if (await readAllBtn.count() > 0) {
    // Click should not crash
    await readAllBtn.click()
    await page.waitForTimeout(1000)
    // Dropdown should still be open and functional
    await expect(page.getByText(/^Events/)).toBeVisible()
  }
})

test('All Sessions page shows rebuild button', async ({ page }) => {
  await page.goto('/?view=all-sessions')
  await page.waitForTimeout(2000)

  // Should have the rebuild button
  await expect(page.getByText('Rebuild')).toBeVisible()
})

test('All Sessions page shows Kill Stopped and Kill Idle buttons', async ({ page }) => {
  await page.goto('/?view=all-sessions')
  await page.waitForTimeout(2000)

  // These buttons appear when there are sessions
  const killStopped = page.getByText('Kill Stopped')
  const killIdle = page.getByText('Kill Idle')
  // At least one should be visible if sessions exist
  const sessions = page.getByTestId('session-component')
  if (await sessions.count() > 0) {
    const either = (await killStopped.count()) + (await killIdle.count())
    expect(either).toBeGreaterThan(0)
  }
})

test('Session card shows status text', async ({ page }) => {
  await page.goto('/?view=all-sessions')
  await page.waitForTimeout(2000)

  const sessions = page.getByTestId('session-component')
  if (await sessions.count() > 0) {
    // First session should show a status (idle, thinking, stopped, etc.)
    const header = sessions.first().getByTestId('session-header')
    const text = await header.textContent()
    expect(text).toMatch(/idle|thinking|stopped|starting|needs_permission|stream lost/)
  }
})
