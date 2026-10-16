import { expect, test } from '@playwright/test'

test('keeps cached sites visible without skeletons while refreshing after navigation', async ({ page }) => {
  const site = {
    name: 'erp.example.com',
    primary_domain: 'erp.example.com',
    exists: true,
    active_apps: ['frappe', 'erpnext'],
    broken: false,
    provisioning: false,
    setup_complete: true,
  }
  let requests = 0
  let releaseInitial = () => {}
  let releaseRefresh = () => {}
  const initialReady = new Promise<void>((resolve) => { releaseInitial = resolve })
  const refreshReady = new Promise<void>((resolve) => { releaseRefresh = resolve })

  await page.route('**/api/v1/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/v1/storage') {
      await route.fulfill({ json: null })
      return
    }
    let data: object = {}
    if (path.endsWith('/bootstrap')) data = { mode: 'ready', enabled: true, name: 'Production' }
    else if (path.endsWith('/auth/session')) data = { authenticated: true }
    else if (path.endsWith('/notifications')) data = { data: [], meta: { unread: 0, next_cursor: null } }
    else if (path.endsWith('/sites/storage')) data = { sites: [] }
    else if (path.endsWith('/sites')) {
      requests += 1
      const refreshing = requests > 1
      await (refreshing ? refreshReady : initialReady)
      data = [{ ...site, primary_domain: refreshing ? 'updated.example.com' : site.primary_domain }]
    }
    await route.fulfill({ json: data })
  })

  await page.goto('/sites')
  const skeletons = page.locator('.fui-skeleton')
  await expect(skeletons.first()).toBeVisible()
  await expect(page.getByText(site.primary_domain, { exact: true })).toHaveCount(0)
  releaseInitial()
  await expect(page.getByText(site.primary_domain, { exact: true })).toBeVisible()
  await expect(skeletons).toHaveCount(0)

  await page.getByRole('link', { name: 'Storage', exact: true }).click()
  await expect(page).toHaveURL(/\/storage$/)
  await page.getByRole('link', { name: 'Sites', exact: true }).click()
  await expect.poll(() => requests).toBe(2)
  await expect(page.getByText(site.primary_domain, { exact: true })).toBeVisible()
  if (process.env.SITES_SCREENSHOT) {
    await page.screenshot({ path: process.env.SITES_SCREENSHOT, animations: 'disabled' })
  }
  await expect(skeletons).toHaveCount(0)

  releaseRefresh()
  await expect(page.getByText('updated.example.com', { exact: true })).toBeVisible()
  await expect(skeletons).toHaveCount(0)
})
