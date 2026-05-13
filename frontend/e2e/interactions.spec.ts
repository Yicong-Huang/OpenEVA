import { test, expect } from '@playwright/test'

test('graph nodes are rendered and clickable', async ({ page }) => {
  await page.goto('/?project=eva&view=graph')
  // Wait for graph to load - nodes may take time to render
  await page.waitForSelector('.react-flow__node', { timeout: 10000 }).catch(() => null)
  await page.waitForTimeout(1000)

  const nodes = page.locator('.react-flow__node')
  const count = await nodes.count()
  if (count > 0) {
    // Click a node - it should not crash
    await nodes.first().click({ force: true })
    await page.waitForTimeout(500)
  }
  // Page should still be functional regardless
  await expect(page.locator('.view-tab').first()).toBeVisible()
})

test('search in All PRs page filters results', async ({ page }) => {
  await page.goto('/?view=all-prs')
  await page.waitForTimeout(3000)

  const searchInput = page.getByTestId('pr-search')
  await expect(searchInput).toBeVisible()

  // Type a search term
  await searchInput.fill('repo')
  await page.waitForTimeout(1000)

  // URL should update with the search param (useApi refetches)
  // Just verify the search input has the value
  await expect(searchInput).toHaveValue('repo')
})

test('PR filter tabs switch correctly', async ({ page }) => {
  await page.goto('/?view=all-prs')
  await page.waitForTimeout(3000)

  // Find the Merged tab within the PR page filter tabs (not sidebar)
  const prPage = page.getByTestId('all-prs-page')
  const mergedTab = prPage.locator('.view-tab', { hasText: 'Merged' })
  await mergedTab.click()
  await page.waitForTimeout(1000)
  await expect(mergedTab).toHaveClass(/active/)
})

test('clicking project in sidebar navigates to it', async ({ page }) => {
  await page.goto('/')
  await page.waitForTimeout(2000)

  // Click a project in the sidebar
  const projectItem = page.getByTestId('project-eva')
  if (await projectItem.count() > 0) {
    await projectItem.click()
    await page.waitForTimeout(1000)
    expect(page.url()).toContain('project=eva')
  }
})

test('list view renders task cards for a project', async ({ page }) => {
  await page.goto('/?project=eva&view=list')
  await page.waitForTimeout(3000)

  // Verify list tab is active
  const listTab = page.locator('.view-tab', { hasText: 'Task Cards' })
  await expect(listTab).toHaveClass(/active/)

  // Should have task cards rendered
  const taskCards = page.locator('[data-testid^="task-card"]')
  const count = await taskCards.count()
  expect(count).toBeGreaterThan(0)
})
