import { expect, test } from '@playwright/test'
import { readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

const dataDir = process.env.AFN_DATA_DIR!
const token = () => readFileSync(join(dataDir, 'api_token'), 'utf8').trim()
const go = (page: import('@playwright/test').Page, label: string) => page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: new RegExp('^' + label) }).click()

const AGENDA = 'SYNTHETIC agenda\nWelcome\nOrganizations\n  Relief Society\n  Elders Quorum\nBudget review\nClosing'
const TRANSCRIPT = 'SYNTHETIC transcript, fictional people.\nSpeaker 1: Relief Society compassionate service plan was discussed and a meal rotation proposed.\nSpeaker 2: Budget review: we should follow up on the youth budget next month.\nSpeaker 1: Closing remarks.'

test.beforeAll(() => {
  // Point the app at the fake model (UI Settings would do the same thing).
  writeFileSync(join(dataDir, 'settings.json'), JSON.stringify({ models: { primary_model: 'fake-model', max_review_rounds: 1 }, theme: 'system' }))
})

test('requires the launch token', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByText('Access token required')).toBeVisible()
})

test('full workflow: agenda → transcript → generate → review → approve → copy, then workspace isolation', async ({ page, context }) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write'])
  await page.goto(`/#token=${token()}`)
  await expect(page.getByRole('heading', { name: 'Ward Council' })).toBeVisible()

  await go(page, 'New Meeting')
  await page.getByLabel('Meeting date').fill('2026-03-01')
  await page.getByRole('button', { name: 'Create meeting' }).click()
  await expect(page).toHaveURL(/\/meetings\//)

  await page.getByRole('button', { name: 'Text' }).click()
  await page.getByLabel('Agenda text').fill(AGENDA)
  await page.getByRole('button', { name: 'Use this text' }).click()
  await expect(page.getByTestId('agenda-editor').getByRole('textbox')).toHaveCount(7)
  // Edit one heading in the tree, then approve.
  await page.getByTestId('agenda-editor').getByRole('textbox').nth(4).fill('Elders Quorum ')
  await page.getByTestId('approve-agenda').click()
  await expect(page.getByText(/approved — revision 2/)).toBeVisible()

  await page.getByTestId('transcript').fill(TRANSCRIPT)
  await expect(page.getByText('Saved')).toBeVisible()

  await page.getByTestId('generate').click()
  await expect(page.getByText(/^done: /)).toBeVisible({ timeout: 30_000 })
  await expect(page.getByTestId('markdown')).toContainText('Next Meeting – Follow-Up Items')
  const md = await page.getByTestId('markdown').innerText()
  expect(md).toContain('- Organizations\n  - Relief Society\n    - Discussion indicated:')
  expect(md).toContain('  - Elders Quorum \n    - Not discussed.')
  expect(md).toContain('- Welcome\n  - Not discussed.')
  expect(md.indexOf('- Welcome')).toBeLessThan(md.indexOf('- Closing'))

  // Evidence highlighting.
  await page.getByTestId('note').first().getByRole('button').first().click()
  await expect(page.getByTestId('evidence').locator('mark')).toHaveCount(1)

  // Manual edit becomes "User corrected"/"Manual, unverified", never "Source verified".
  const noteText = page.getByTestId('note').first().getByLabel('Note text')
  await noteText.fill('Meal rotation proposed for compassionate service; no decision recorded.')
  await noteText.blur()
  await expect(page.getByTestId('note').first().getByText(/Manual, unverified|User corrected/)).toBeVisible()

  await page.getByTestId('approve-notes').click()
  await expect(page.getByText('approved', { exact: true })).toBeVisible()
  await page.getByTestId('copy-md').click()
  await expect(page.getByText('Copied!')).toBeVisible()
  const clip = await page.evaluate(() => navigator.clipboard.readText())
  expect(clip).toContain('Ward Council Meeting Notes – March 1, 2026')
  expect(clip).toContain('Next Meeting – Follow-Up Items')

  // History shows it; Bishopric sees nothing.
  await go(page, 'Meeting History')
  await expect(page.getByRole('cell', { name: 'March 1, 2026' })).toBeVisible()
  await page.getByTestId('workspace-select').selectOption('bishopric')
  await go(page, 'Meeting History')
  await expect(page.getByText('No meetings yet.')).toBeVisible()
  await go(page, 'Search')
  await page.getByPlaceholder('Keyword, topic, decision…').fill('rotation')
  await page.getByRole('button', { name: 'Search' }).click()
  await expect(page.getByText('No matches.')).toBeVisible()
  await page.getByTestId('workspace-select').selectOption('ward_council')
  await go(page, 'Search')
  await page.getByPlaceholder('Keyword, topic, decision…').fill('rotation')
  await page.getByRole('button', { name: 'Search' }).click()
  await expect(page.getByText('Open meeting')).toBeVisible()
})

test('dark mode toggle and settings page load', async ({ page }) => {
  await page.goto(`/#token=${token()}`)
  await page.getByLabel('Theme').selectOption('dark')
  await expect(page.locator('html')).toHaveClass(/dark/)
  await go(page, 'Settings')
  await expect(page.getByText('Ollama connection')).toBeVisible()
  await expect(page.getByText('Connected')).toBeVisible()
})
