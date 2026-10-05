import { render, screen, fireEvent } from '@testing-library/react';
import { describe, expect, test, vi } from 'vitest';
import { ElasticsearchForm } from './ElasticsearchForm';
import { FamilyFormRouter } from './FamilyFormRouter';

function lastArg(fn: ReturnType<typeof vi.fn>): Record<string, unknown> {
  return fn.mock.calls.at(-1)![0] as Record<string, unknown>;
}

// P1c: the Sources wizard offered no Elasticsearch / OpenSearch form at all.
describe('ElasticsearchForm', () => {
  test('url, index pattern and the sort field go to connection_config', () => {
    const onChange = vi.fn();
    render(<ElasticsearchForm sourceType="elasticsearch" value={{}} onChange={onChange} />);
    fireEvent.change(screen.getByLabelText('Cluster URL'), { target: { value: 'https://es.example.com:9200' } });
    expect(lastArg(onChange)).toEqual({ url: 'https://es.example.com:9200' });
    fireEvent.change(screen.getByLabelText('Index'), { target: { value: 'logs-*' } });
    expect(lastArg(onChange)).toEqual({ index: 'logs-*' });
    expect(screen.getByLabelText('Sort field')).toHaveAttribute('placeholder', '@timestamp');
  });

  test('basic auth: username plus a masked password', () => {
    const onChange = vi.fn();
    render(<ElasticsearchForm sourceType="elasticsearch" value={{ auth_mode: 'basic' }} onChange={onChange} />);
    fireEvent.change(screen.getByLabelText('Username'), { target: { value: 'reader' } });
    expect(lastArg(onChange)).toMatchObject({ username: 'reader' });
    expect(screen.getByLabelText('Password')).toHaveAttribute('type', 'password');
    expect(screen.queryByLabelText('API key')).not.toBeInTheDocument();
  });

  test('API key auth: a masked API key, no username', () => {
    const onChange = vi.fn();
    render(<ElasticsearchForm sourceType="elasticsearch" value={{ auth_mode: 'api_key' }} onChange={onChange} />);
    const key = screen.getByLabelText('API key');
    expect(key).toHaveAttribute('type', 'password');
    fireEvent.change(key, { target: { value: 'aWQ6a2V5' } });
    expect(lastArg(onChange)).toMatchObject({ api_key: 'aWQ6a2V5' });
    expect(screen.queryByLabelText('Username')).not.toBeInTheDocument();
  });

  test('switching the auth mode drops the other credentials', () => {
    const onChange = vi.fn();
    render(<ElasticsearchForm sourceType="elasticsearch" value={{ auth_mode: 'api_key', api_key: 'k' }} onChange={onChange} />);
    fireEvent.change(screen.getByLabelText('Authentication'), { target: { value: 'basic' } });
    expect(lastArg(onChange)).toEqual({ auth_mode: 'basic' });
  });

  test('a stored API key (masked) selects API key auth when editing', () => {
    render(<ElasticsearchForm sourceType="elasticsearch" value={{ api_key: '********' }} onChange={vi.fn()} />);
    expect(screen.getByLabelText('API key')).toBeInTheDocument();
  });

  test('the family router sends elasticsearch and opensearch to this form', () => {
    for (const t of ['elasticsearch', 'opensearch']) {
      const { unmount } = render(<FamilyFormRouter family="nosql_database" sourceType={t} value={{}} onChange={vi.fn()} />);
      expect(screen.getByLabelText('Cluster URL')).toBeInTheDocument();
      unmount();
    }
  });
});
