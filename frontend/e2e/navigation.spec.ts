import { test, expect } from '@playwright/test'

test('switching between graph/list/sessions views', async ({ page }) => {
  await page.goto('/?project=eva&view=graph')
  await page.waitForTimeout(2000)

  // Graph view should be active
  const graphTab = page.locator('.view-tab', { hasText: 'Task Tracker' })
  await expect(graphTab).toHaveClass(/active/)

  // Switch to list view
  const listTab = page.locator('.view-tab', { hasText: 'Task Cards' })
  await listTab.click()
  await page.waitForTimeout(1000)
  await expect(listTab).toHaveClass(/active/)
  expect(page.url()).toContain('view=list')

  // Switch to sessions view
  const sessionsTab = page.locator('.view-tab', { hasText: 'Sessions' })
  await sessionsTab.click()
  await page.waitForTimeout(1000)
  await expect(sessionsTab).toHaveClass(/active/)
  expect(page.url()).toContain('view=sessions')
})

test('AI Usage dropdown opens and shows data', async ({ page }) => {
  await page.goto('/')
  await page.waitForTimeout(3000)

  const usageTopbar = page.getByTestId('usage-topbar')
  await expect(usageTopbar).toBeVisible()

  // Click the inner span with the onClick handler
  await usageTopbar.locator('> span').click()
  await page.waitForTimeout(1000)
  // The dropdown should contain "AI Usage" heading and all three labels
  await expect(page.getByText('AI Usage', { exact: true })).toBeVisible()
  await expect(page.getByText('Daily').first()).toBeVisible()
  await expect(page.getByText('Monthly').first()).toBeVisible()
})

test('Auth Status dropdown opens and shows certs', async ({ page }) => {
  await page.goto('/')
  await page.waitForTimeout(3000)

  const certsTopbar = page.getByTestId('certs-topbar')
  await expect(certsTopbar).toBeVisible()

  await certsTopbar.locator('> span').click()
  await page.waitForTimeout(1000)
  await expect(page.locator('text=Auth Status')).toBeVisible()
})

test('Events dropdown opens and shows events', async ({ page }) => {
  await page.goto('/')
  await page.waitForTimeout(3000)

  const eventsTopbar = page.getByTestId('events-topbar')
  await expect(eventsTopbar).toBeVisible()

  await eventsTopbar.locator('> span').click()
  await page.waitForTimeout(1000)
  // Dropdown should show "Events (N)" heading
  await expect(page.getByText(/^Events\s*\(\d+\)$/)).toBeVisible()
})

test('project progress bar renders', async ({ page }) => {
  await page.goto('/?project=eva&view=graph')
  await page.waitForTimeout(2000)

  // Should have progress bar
  const progressBar = page.locator('.progress-bar')
  await expect(progressBar).toBeVisible()

  // Should have task counts
  const taskCounts = page.locator('.task-counts')
  await expect(taskCounts).toBeVisible()
})

test('list view shows task cards', async ({ page }) => {
  await page.goto('/?project=eva&view=list')
  await page.waitForTimeout(3000)

  // Should show at least one task card (expanded or mini)
  const taskCards = page.locator('[data-testid^="task-card"]')
  const count = await taskCards.count()
  expect(count).toBeGreaterThan(0)
})
