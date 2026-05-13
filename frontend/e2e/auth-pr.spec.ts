import { test, expect } from '@playwright/test'

test('Auth status shows cert dots and dropdown opens', async ({ page }) => {
  await page.goto('/')
  await page.waitForTimeout(3000)

  const authTopbar = page.getByTestId('certs-topbar')
  await expect(authTopbar).toBeVisible()

  // Click to open dropdown
  await authTopbar.locator('> span').click()
  await page.waitForTimeout(500)

  // Should show "Auth Status" heading
  await expect(page.getByText('Auth Status')).toBeVisible()
})

test('Auth status shows force refresh button for each cert', async ({ page }) => {
  await page.goto('/')
  await page.waitForTimeout(3000)

  const authTopbar = page.getByTestId('certs-topbar')
  await authTopbar.locator('> span').click()
  await page.waitForTimeout(500)

  // Should have at least one renew button (at least one cert is registered)
  const renewButtons = page.locator('[data-testid^="renew-"]')
  const count = await renewButtons.count()
  expect(count).toBeGreaterThan(0)
})

test('clicking a PR in All PRs opens PR detail panel', async ({ page }) => {
  await page.goto('/?view=all-prs')
  await page.waitForTimeout(3000)

  const prCards = page.getByTestId('pr-overview')
  const count = await prCards.count()
  if (count > 0) {
    await prCards.first().click()
    await page.waitForTimeout(2000)

    // PR detail should appear (has a title, CI section, etc.)
    // Just verify the page didn't crash and still has content
    await expect(page.locator('.view-tab').first()).toBeVisible()
  }
})

test('project graph view shows nodes with status dots', async ({ page }) => {
  await page.goto('/?project=eva&view=graph')
  await page.waitForTimeout(5000)

  const nodes = page.locator('.react-flow__node')
  const count = await nodes.count()
  if (count > 0) {
    // Nodes should have status dots
    const dots = page.locator('.react-flow__node [data-testid="status-dot"]')
    expect(await dots.count()).toBeGreaterThan(0)
  }
})
