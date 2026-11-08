// Captures README screenshots with synthetic data. Run: AFN_SCREENSHOTS=1 npx playwright test screenshots
import { expect, test } from '@playwright/test'
import { readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

test.skip(!process.env.AFN_SCREENSHOTS, 'set AFN_SCREENSHOTS=1')
const dataDir = process.env.AFN_DATA_DIR!
const out = (n: string) => join(process.cwd(), '..', 'docs', 'screenshots', n)
const go = (page: import('@playwright/test').Page, label: string) => page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: new RegExp('^' + label) }).click()

const AGENDA = 'Welcome and Opening Prayer\nMinistering\nOrganizations\n  Relief Society\n    Compassionate service plan\n  Elders Quorum\n    Temple trip\n  Primary\nYouth Conference planning\nClosing'
const TRANSCRIPT = `SYNTHETIC transcript – fictional people.
Speaker 1: Ministering interviews are mostly done, about eighty percent.
Speaker 2: For Relief Society, the compassionate service plan: we decided to adopt the meal rotation starting next month.
Speaker 3: Rowan Castellano, can you draft the rotation list? Rowan: Yes, I'll draft the rotation list by the fifteenth. Please follow up with me.
Speaker 2: Temple trip, maybe May or June? Not sure yet. No decision today.
Speaker 1: Youth Conference planning: Pat will look into the bus quote. Follow up next time.
Speaker 1: Closing remarks.`

test('capture screenshots', async ({ page }) => {
  writeFileSync(join(dataDir, 'settings.json'), JSON.stringify({ models: { primary_model: 'fake-model', max_review_rounds: 1 }, theme: 'system' }))
  const token = readFileSync(join(dataDir, 'api_token'), 'utf8').trim()
  await page.setViewportSize({ width: 1400, height: 900 })
  await page.goto(`/#token=${token}`)
  await go(page, 'New Meeting')
  await page.getByLabel('Meeting date').fill('2026-03-01')
  await page.getByPlaceholder(/meeting$/).fill('Ward Council')
  await page.getByRole('button', { name: 'Create meeting' }).click()
  await page.getByRole('button', { name: 'Text' }).click()
  await page.getByLabel('Agenda text').fill(AGENDA)
  await page.getByRole('button', { name: 'Use this text' }).click()
  await page.screenshot({ path: out('01-agenda-editor.png') })
  await page.getByTestId('approve-agenda').click()
  await page.getByTestId('transcript').fill(TRANSCRIPT)
  await expect(page.getByText('Saved')).toBeVisible()
  await page.getByTestId('generate').click()
  await expect(page.getByText(/^done: /)).toBeVisible({ timeout: 30_000 })
  await page.getByTestId('note').first().getByRole('button').first().click()
  await page.getByText('4. Review evidence').scrollIntoViewIfNeeded()
  await page.screenshot({ path: out('02-review-evidence.png') })
  await page.getByTestId('approve-notes').click()
  await expect(page.getByText('approved', { exact: true })).toBeVisible()
  await page.getByTestId('markdown').scrollIntoViewIfNeeded()
  await page.screenshot({ path: out('03-approved-markdown.png') })
  await go(page, 'Follow-Up Inbox')
  await page.screenshot({ path: out('04-follow-up-inbox.png') })
  await go(page, 'Dashboard')
  await page.getByLabel('Theme').selectOption('dark')
  await page.screenshot({ path: out('05-dashboard-dark.png') })
})
