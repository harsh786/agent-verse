import { expect, test, beforeEach } from 'vitest';
import { useToastStore, toast } from '@/stores/toast';

beforeEach(() => useToastStore.setState({ toasts: [] }));

test('toast() adds an item and returns its id', () => {
  const id = toast({ kind: 'error', message: 'Boom' });
  const items = useToastStore.getState().toasts;
  expect(items).toHaveLength(1);
  expect(items[0]).toMatchObject({ id, kind: 'error', message: 'Boom' });
});

test('dismiss removes the item', () => {
  const id = toast({ kind: 'info', message: 'Hi' });
  useToastStore.getState().dismiss(id);
  expect(useToastStore.getState().toasts).toHaveLength(0);
});
