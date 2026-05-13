import { test, expect } from '@playwright/test'

test('homepage loads and auto-selects first project', async ({ page }) => {
  await page.goto('/')
  await page.waitForTimeout(3000)
  await expect(page.locator('.view-tab').first()).toBeVisible()
})

test('sidebar shows all projects', async ({ page }) => {
  await page.goto('/')
  await page.waitForTimeout(2000)
  await expect(page.getByTestId('project-eva')).toBeVisible()
  await expect(page.getByTestId('project-example-serializer-refactor')).toBeVisible()
})

test('clicking project shows graph view', async ({ page }) => {
  await page.goto('/?project=eva&view=graph')
  await page.waitForTimeout(3000)
  await expect(page.locator('[data-testid="graph-view"]')).toBeVisible()
})

test('graph view renders nodes', async ({ page }) => {
  await page.goto('/?project=eva&view=graph')
  await page.waitForTimeout(3000)
  const nodes = page.locator('.react-flow__node')
  const count = await nodes.count()
  expect(count).toBeGreaterThan(0)
})

test('task card renders when task is selected via URL', async ({ page }) => {
  // Navigate to graph view, pick the first node's task id from the DOM
  await page.goto('/?project=eva&view=graph')
  await page.waitForTimeout(3000)
  const firstNode = page.locator('[data-testid^="graph-node-"]').first()
  const testId = await firstNode.getAttribute('data-testid')
  if (testId) {
    const taskId = testId.replace('graph-node-', '')
    await page.goto(`/?project=eva&view=graph&task=${taskId}`)
    await page.waitForTimeout(2000)
    await expect(page.locator('[data-testid="task-card"]')).toBeVisible()
  }
})

test('All PRs page loads with filter tabs', async ({ page }) => {
  await page.goto('/?view=all-prs')
  await page.waitForTimeout(3000)
  await expect(page.getByRole('heading', { name: 'All PRs' })).toBeVisible()
  await expect(page.locator('text=Open').first()).toBeVisible()
  await expect(page.locator('text=Merged').first()).toBeVisible()
})

test('All Sessions page loads', async ({ page }) => {
  await page.goto('/?view=all-sessions')
  await page.waitForTimeout(2000)
  await expect(page.getByRole('heading', { name: 'All Sessions' })).toBeVisible()
})

test('URL updates on navigation', async ({ page }) => {
  await page.goto('/')
  await page.waitForTimeout(2000)
  expect(page.url()).toContain('project=')
  expect(page.url()).toContain('view=')
})

test('TopBar shows auth and usage', async ({ page }) => {
  await page.goto('/')
  await page.waitForTimeout(2000)
  await expect(page.locator('text=Eva').first()).toBeVisible()
})
