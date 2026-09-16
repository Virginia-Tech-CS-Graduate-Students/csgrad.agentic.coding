import { expect, test } from '@playwright/test';
import { resolve } from 'node:path';

test.beforeEach(async ({ request }) => {
  const jobs = await (await request.get('/api/jobs')).json();
  for (const job of jobs) {
    if (['uploading', 'processing'].includes(job.status))
      await request.post(`/api/jobs/${job.id}/cancel`, { headers: { 'X-Local-Request': '1' } });
    await request.delete(`/api/jobs/${job.id}`, { headers: { 'X-Local-Request': '1' } });
  }
});

test('upload, transcript, exports, refresh, and delete', async ({ page, context }) => {
  const errors: string[] = [];
  const remoteRequests: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  page.on('request', (request) => {
    if (!request.url().startsWith('http://127.0.0.1:8766')) remoteRequests.push(request.url());
  });
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Give your words a place.' })).toBeVisible();
  await expect(page.getByText('A fresh start')).toBeVisible();
  await page.screenshot({
    path: `../artifacts/workspace-${test.info().project.name}.png`,
    fullPage: true,
  });
  await page.getByLabel('Choose recording').setInputFiles(resolve('../.cache/media/speech.mp3'));
  await expect(page.getByRole('button', { name: 'Create transcript' })).toBeEnabled();
  await page.getByRole('button', { name: 'Create transcript' }).click();
  await expect(page.getByRole('button', { name: 'Copy text' })).toBeVisible({ timeout: 150_000 });
  await expect(page.locator('.transcript-content')).toContainText('country');
  await page.getByRole('button', { name: 'Copy text' }).click();
  await expect(page.getByRole('button', { name: 'Copied', exact: true })).toBeVisible();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toContain('country');
  await expect(page.locator('.timestamp').first()).toHaveText(/\d\d:\d\d:\d\d/);
  for (const format of ['TXT', 'SRT']) {
    const download = page.waitForEvent('download');
    await page.getByRole('link', { name: format, exact: true }).click();
    expect((await download).suggestedFilename()).toBe(`speech.${format.toLowerCase()}`);
  }
  await page.reload();
  await expect(page.getByRole('heading', { name: 'speech.mp3' })).toBeVisible();
  await expect(page.locator('.transcript-content')).toContainText('country');
  await page.screenshot({
    path: `../artifacts/transcript-${test.info().project.name}.png`,
    fullPage: true,
  });
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
  ).toBeTruthy();
  await page.getByRole('button', { name: 'Delete recording', exact: true }).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await page
    .getByRole('dialog')
    .getByRole('button', { name: 'Delete recording', exact: true })
    .click();
  await expect(page.getByText('A fresh start')).toBeVisible();
  expect(errors).toEqual([]);
  expect(remoteRequests).toEqual([]);
});

test('bad file, retry, cancellation, and no speech', async ({ page }) => {
  await page.goto('/');
  await page
    .getByLabel('Choose recording')
    .setInputFiles({ name: 'bad.wav', mimeType: 'audio/wav', buffer: Buffer.from('bad') });
  await expect(page.getByRole('alert')).toContainText('MP3');
  await page.getByLabel('Choose recording').setInputFiles({
    name: 'broken.mp3',
    mimeType: 'audio/mpeg',
    buffer: Buffer.from('bad audio'),
  });
  await page.getByRole('button', { name: 'Create transcript' }).click();
  await expect(page.getByText('This recording needs attention.')).toBeVisible({ timeout: 30_000 });
  await page.getByRole('button', { name: 'Retry transcription' }).click();
  await expect(page.getByText('This recording needs attention.')).toBeVisible({ timeout: 30_000 });
  await page.getByRole('button', { name: 'New transcript' }).click();
  await page.getByLabel('Choose recording').setInputFiles(resolve('../.cache/media/speech.mp4'));
  await page.getByRole('button', { name: 'Create transcript' }).click();
  await page.getByRole('button', { name: 'Cancel transcription' }).click();
  await expect(page.getByRole('heading', { name: 'Transcription cancelled' })).toBeVisible();
  await page.getByRole('button', { name: 'New transcript' }).click();
  await page.getByLabel('Choose recording').setInputFiles(resolve('../.cache/media/silence.mp3'));
  await page.getByRole('button', { name: 'Create transcript' }).click();
  await expect(page.getByRole('heading', { name: 'No speech detected' })).toBeVisible({
    timeout: 150_000,
  });
});

test('keyboard file selection and drag-and-drop', async ({ page }) => {
  await page.goto('/');
  const button = page.getByRole('button', { name: 'Choose a file', exact: true });
  await button.focus();
  const chooser = page.waitForEvent('filechooser');
  await page.keyboard.press('Enter');
  await (await chooser).setFiles(resolve('../.cache/media/speech.mp3'));
  await expect(page.getByRole('heading', { name: 'speech.mp3' })).toBeVisible();
  const transfer = await page.evaluateHandle(() => {
    const data = new DataTransfer();
    data.items.add(new File(['placeholder'], 'dropped.mp4', { type: 'video/mp4' }));
    return data;
  });
  await page.locator('.drop-zone').dispatchEvent('drop', { dataTransfer: transfer });
  await expect(page.getByRole('heading', { name: 'dropped.mp4' })).toBeVisible();
  await transfer.dispose();
});
