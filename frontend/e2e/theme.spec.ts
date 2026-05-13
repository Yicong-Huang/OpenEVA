import { test, expect } from '@playwright/test'

test.describe('Theme system', () => {
  test.beforeEach(async ({ page }) => {
    // Clear theme from localStorage before each test
    await page.goto('/')
    await page.evaluate(() => localStorage.removeItem('eva-theme'))
    await page.reload()
    await page.waitForTimeout(2000)
  })

  test('defaults to dark theme', async ({ page }) => {
    const theme = await page.evaluate(() =>
      document.documentElement.getAttribute('data-theme')
    )
    expect(theme).toBe('dark')

    // Verify dark colors are applied
    const bg = await page.evaluate(() =>
      getComputedStyle(document.documentElement).getPropertyValue('--bg').trim()
    )
    expect(bg).toBe('#0f0f17')
  })

  test('menu shows Light Mode option', async ({ page }) => {
    await page.getByTestId('menu-btn').click()
    await expect(page.getByTestId('theme-toggle')).toBeVisible()
    await expect(page.getByTestId('theme-toggle')).toHaveText('Light Mode')
  })

  test('clicking Light Mode switches to light theme', async ({ page }) => {
    await page.getByTestId('menu-btn').click()
    await page.getByTestId('theme-toggle').click()

    const theme = await page.evaluate(() =>
      document.documentElement.getAttribute('data-theme')
    )
    expect(theme).toBe('light')

    // Verify light colors
    const bg = await page.evaluate(() =>
      getComputedStyle(document.documentElement).getPropertyValue('--bg').trim()
    )
    expect(bg).toBe('#f5f5f7')
  })

  test('theme persists across page reload', async ({ page }) => {
    // Switch to light
    await page.getByTestId('menu-btn').click()
    await page.getByTestId('theme-toggle').click()

    // Reload
    await page.reload()
    await page.waitForTimeout(1000)

    const theme = await page.evaluate(() =>
      document.documentElement.getAttribute('data-theme')
    )
    expect(theme).toBe('light')
  })

  test('light mode menu shows Dark Mode option', async ({ page }) => {
    // Switch to light
    await page.getByTestId('menu-btn').click()
    await page.getByTestId('theme-toggle').click()

    // Open menu again
    await page.getByTestId('menu-btn').click()
    await expect(page.getByTestId('theme-toggle')).toHaveText('Dark Mode')
  })

  test('toggling back to dark restores dark colors', async ({ page }) => {
    // Switch to light
    await page.getByTestId('menu-btn').click()
    await page.getByTestId('theme-toggle').click()

    // Switch back to dark
    await page.getByTestId('menu-btn').click()
    await page.getByTestId('theme-toggle').click()

    const bg = await page.evaluate(() =>
      getComputedStyle(document.documentElement).getPropertyValue('--bg').trim()
    )
    expect(bg).toBe('#0f0f17')
  })
})
