import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, test, vi } from 'vitest';
import { Tabs, TabsList, TabsTrigger, TabsContent } from './tabs';

describe('Tabs', () => {
  test('uncontrolled: defaultValue selects the initial tab and clicking switches content', async () => {
    render(
      <Tabs defaultValue="a">
        <TabsList>
          <TabsTrigger value="a">A</TabsTrigger>
          <TabsTrigger value="b">B</TabsTrigger>
        </TabsList>
        <TabsContent value="a">Panel A</TabsContent>
        <TabsContent value="b">Panel B</TabsContent>
      </Tabs>,
    );
    expect(screen.getByText('Panel A')).toBeInTheDocument();
    expect(screen.queryByText('Panel B')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'A' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('button', { name: 'B' })).toHaveAttribute('aria-selected', 'false');

    await userEvent.click(screen.getByRole('button', { name: 'B' }));
    expect(screen.queryByText('Panel A')).not.toBeInTheDocument();
    expect(screen.getByText('Panel B')).toBeInTheDocument();
  });

  test('uncontrolled: with no defaultValue, active starts as empty string so no content matches', () => {
    render(
      <Tabs>
        <TabsList>
          <TabsTrigger value="a">A</TabsTrigger>
        </TabsList>
        <TabsContent value="a">Panel A</TabsContent>
      </Tabs>,
    );
    expect(screen.queryByText('Panel A')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'A' })).toHaveAttribute('aria-selected', 'false');
  });

  test('controlled: value prop drives the active tab and onValueChange fires on click', async () => {
    const onValueChange = vi.fn();
    const { rerender } = render(
      <Tabs value="a" onValueChange={onValueChange}>
        <TabsList>
          <TabsTrigger value="a">A</TabsTrigger>
          <TabsTrigger value="b">B</TabsTrigger>
        </TabsList>
        <TabsContent value="a">Panel A</TabsContent>
        <TabsContent value="b">Panel B</TabsContent>
      </Tabs>,
    );
    expect(screen.getByText('Panel A')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'B' }));
    expect(onValueChange).toHaveBeenCalledWith('b');
    // Controlled: clicking alone doesn't change content until the parent re-renders with the new value.
    expect(screen.getByText('Panel A')).toBeInTheDocument();

    rerender(
      <Tabs value="b" onValueChange={onValueChange}>
        <TabsList>
          <TabsTrigger value="a">A</TabsTrigger>
          <TabsTrigger value="b">B</TabsTrigger>
        </TabsList>
        <TabsContent value="a">Panel A</TabsContent>
        <TabsContent value="b">Panel B</TabsContent>
      </Tabs>,
    );
    expect(screen.getByText('Panel B')).toBeInTheDocument();
    expect(screen.queryByText('Panel A')).not.toBeInTheDocument();
  });

  test('applies custom className to Tabs root and TabsList/TabsTrigger/TabsContent', () => {
    const { container } = render(
      <Tabs defaultValue="a" className="root-cls">
        <TabsList className="list-cls">
          <TabsTrigger value="a" className="trigger-cls">A</TabsTrigger>
        </TabsList>
        <TabsContent value="a" className="content-cls">Panel A</TabsContent>
      </Tabs>,
    );
    expect(container.querySelector('.root-cls')).toBeInTheDocument();
    expect(container.querySelector('.list-cls')).toBeInTheDocument();
    expect(container.querySelector('.trigger-cls')).toBeInTheDocument();
    expect(container.querySelector('.content-cls')).toBeInTheDocument();
  });
});
