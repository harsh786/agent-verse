/**
 * State Machines — E2E Tests
 *
 * Covers /state-machines (src/features/state-machines/StateMachinesPage.tsx).
 *
 * Endpoints mocked:
 *   GET    /state-machines
 *   GET    /state-machines/{id}
 *   POST   /state-machines
 *   DELETE /state-machines/{id}
 */
import { test, expect, type Page } from '@playwright/test';
import { setupAuth } from './helpers/auth';

// ── Mock data ─────────────────────────────────────────────────────────────────

interface MachineListItem {
  machine_id: string;
  name: string;
  state_count: number;
}

interface MachineDetail {
  machine_id: string;
  name: string;
  states: Array<{ name: string; is_initial: boolean; is_terminal: boolean }>;
  transitions: Array<{ from_state: string; to_state: string; event: string }>;
}

const MACHINE_LIST: MachineListItem = { machine_id: 'sm-1', name: 'Order Flow', state_count: 3 };

const MACHINE_DETAIL: MachineDetail = {
  machine_id: 'sm-1',
  name: 'Order Flow',
  states: [
    { name: 'pending', is_initial: true, is_terminal: false },
    { name: 'processing', is_initial: false, is_terminal: false },
    { name: 'completed', is_initial: false, is_terminal: true },
  ],
  transitions: [
    { from_state: 'pending', to_state: 'processing', event: 'start' },
    { from_state: 'processing', to_state: 'completed', event: 'complete' },
  ],
};

async function mockStateMachinesApi(
  page: Page,
  { machines = [] as MachineListItem[], details = {} as Record<string, MachineDetail> } = {}
): Promise<void> {
  let currentList = [...machines];

  await page.route(/\/state-machines\/[^/?]+$/, (route) => {
    const method = route.request().method();
    const id = route.request().url().split('/state-machines/')[1].split('?')[0];

    if (method === 'DELETE') {
      currentList = currentList.filter((m) => m.machine_id !== id);
      return route.fulfill({ status: 204, body: '' });
    }
    // GET detail
    const detail = details[id];
    return route.fulfill({
      status: detail ? 200 : 404,
      contentType: 'application/json',
      body: JSON.stringify(detail ?? { detail: 'not found' }),
    });
  });

  await page.route('**/state-machines', (route) => {
    const method = route.request().method();
    if (method === 'POST') {
      const body = JSON.parse(route.request().postData() ?? '{}');
      const created: MachineListItem = {
        machine_id: 'sm-new',
        name: body.name ?? 'Untitled',
        state_count: Array.isArray(body.states) ? body.states.length : 0,
      };
      currentList = [...currentList, created];
      return route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify(created) });
    }
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(currentList) });
  });
}

test.describe('State Machines — empty state', () => {
  test('1. Shows empty-state message when there are no machines', async ({ page }) => {
    await setupAuth(page);
    await mockStateMachinesApi(page, { machines: [] });
    await page.goto('/state-machines');

    await expect(page.getByRole('heading', { name: 'State Machines' })).toBeVisible({ timeout: 10000 });
    await expect(page.getByText(/no state machines yet/i)).toBeVisible();
    await expect(page.getByText(/select a state machine to view details/i)).toBeVisible();
  });
});

test.describe('State Machines — populated state', () => {
  test('2. Renders the machine list and loads detail (states + transitions) on selection', async ({ page }) => {
    await setupAuth(page);
    await mockStateMachinesApi(page, {
      machines: [MACHINE_LIST],
      details: { 'sm-1': MACHINE_DETAIL },
    });
    await page.goto('/state-machines');

    await expect(page.getByText('Order Flow')).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('3 states')).toBeVisible();

    await page.getByText('Order Flow').click();

    await expect(page.getByRole('heading', { name: 'Order Flow' })).toBeVisible({ timeout: 5000 });
    await expect(page.getByText(/pending.*\(initial\)/i)).toBeVisible();
    await expect(page.getByText(/completed.*\(terminal\)/i)).toBeVisible();
    await expect(page.getByText('–start→')).toBeVisible();
    await expect(page.getByText('–complete→')).toBeVisible();
  });

  test('3. Shows an error banner when the list request fails', async ({ page }) => {
    await setupAuth(page);
    await page.route('**/state-machines', (route) =>
      route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ detail: 'boom' }) })
    );
    await page.goto('/state-machines');

    await expect(page.getByRole('heading', { name: 'State Machines' })).toBeVisible({ timeout: 10000 });
    await expect(page.getByText(/failed to load state machines/i)).toBeVisible({ timeout: 5000 });
  });
});

test.describe('State Machines — primary interaction', () => {
  test('4. Creating a new state machine adds it to the list', async ({ page }) => {
    await setupAuth(page);
    await mockStateMachinesApi(page, { machines: [] });
    await page.goto('/state-machines');

    await expect(page.getByText(/no state machines yet/i)).toBeVisible({ timeout: 10000 });
    await page.getByRole('button', { name: /new state machine/i }).click();

    await expect(page.getByRole('dialog', { name: /create state machine/i })).toBeVisible();
    await page.getByLabel('Name').fill('Ticket Lifecycle');
    await page.getByRole('button', { name: 'Create', exact: true }).click();

    await expect(page.getByText('Ticket Lifecycle')).toBeVisible({ timeout: 5000 });
  });

  test('5. Deleting a machine from the list removes it', async ({ page }) => {
    await setupAuth(page);
    await mockStateMachinesApi(page, {
      machines: [MACHINE_LIST],
      details: { 'sm-1': MACHINE_DETAIL },
    });
    await page.goto('/state-machines');

    await expect(page.getByText('Order Flow')).toBeVisible({ timeout: 10000 });
    await page.getByLabel('Delete Order Flow').click();

    await expect(page.getByText(/no state machines yet/i)).toBeVisible({ timeout: 5000 });
  });

  test('6. Cancel closes the create-machine modal without creating anything', async ({ page }) => {
    await setupAuth(page);
    await mockStateMachinesApi(page, { machines: [] });
    await page.goto('/state-machines');

    await expect(page.getByText(/no state machines yet/i)).toBeVisible({ timeout: 10000 });
    await page.getByRole('button', { name: /new state machine/i }).click();
    await expect(page.getByRole('dialog', { name: /create state machine/i })).toBeVisible();

    await page.getByRole('button', { name: 'Cancel' }).click();

    await expect(page.getByRole('dialog', { name: /create state machine/i })).not.toBeVisible();
    await expect(page.getByText(/no state machines yet/i)).toBeVisible();
  });
});
