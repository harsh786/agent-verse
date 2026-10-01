import { render, screen, fireEvent } from '@testing-library/react';
import { describe, expect, test, vi } from 'vitest';
import { RedisForm } from './RedisForm';

function lastArg(fn: ReturnType<typeof vi.fn>): Record<string, unknown> {
  return fn.mock.calls.at(-1)![0] as Record<string, unknown>;
}

describe('RedisForm', () => {
  test('standalone with no auth shows host/port/db and no password field', () => {
    render(<RedisForm sourceType="redis" value={{}} onChange={vi.fn()} />);
    expect(screen.getByLabelText('Host')).toBeInTheDocument();
    expect(screen.getByLabelText('Port')).toHaveValue(6379);
    expect(screen.queryByLabelText('Password')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Username')).not.toBeInTheDocument();
  });

  test('the Redis URL is a masked secret', () => {
    const onChange = vi.fn();
    render(<RedisForm sourceType="redis" value={{}} onChange={onChange} />);
    const uri = screen.getByLabelText('Redis URL (optional)');
    expect(uri).toHaveAttribute('type', 'password');
    fireEvent.change(uri, { target: { value: 'rediss://u:p@h:6380/1' } });
    expect(lastArg(onChange)).toEqual({ uri: 'rediss://u:p@h:6380/1' });
  });

  test('password auth shows a masked password only', () => {
    const onChange = vi.fn();
    render(<RedisForm sourceType="redis" value={{ auth_type: 'password' }} onChange={onChange} />);
    expect(screen.queryByLabelText('Username')).not.toBeInTheDocument();
    const pw = screen.getByLabelText('Password');
    expect(pw).toHaveAttribute('type', 'password');
    fireEvent.change(pw, { target: { value: 's3cret' } });
    expect(lastArg(onChange)).toEqual({ auth_type: 'password', password: 's3cret' });
  });

  test('ACL auth adds the username', () => {
    const onChange = vi.fn();
    render(<RedisForm sourceType="redis" value={{ auth_type: 'acl' }} onChange={onChange} />);
    fireEvent.change(screen.getByLabelText('Username'), { target: { value: 'alice' } });
    expect(lastArg(onChange)).toEqual({ auth_type: 'acl', username: 'alice' });
    expect(screen.getByLabelText('Password')).toHaveAttribute('type', 'password');
  });

  test('sentinel mode asks for sentinels, master name and a masked sentinel password', () => {
    const onChange = vi.fn();
    render(<RedisForm sourceType="redis" value={{ mode: 'sentinel' }} onChange={onChange} />);
    expect(screen.queryByLabelText('Host')).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Sentinels'), { target: { value: 's1:26379' } });
    expect(lastArg(onChange)).toEqual({ mode: 'sentinel', sentinels: 's1:26379' });
    fireEvent.change(screen.getByLabelText('Master name'), { target: { value: 'mymaster' } });
    expect(lastArg(onChange)).toEqual({ mode: 'sentinel', sentinel_master: 'mymaster' });
    expect(screen.getByLabelText('Sentinel password (optional)')).toHaveAttribute('type', 'password');
  });

  test('cluster mode asks for seed nodes', () => {
    const onChange = vi.fn();
    render(<RedisForm sourceType="redis" value={{ mode: 'cluster' }} onChange={onChange} />);
    fireEvent.change(screen.getByLabelText('Cluster nodes'), { target: { value: 'n1:7000,n2:7001' } });
    expect(lastArg(onChange)).toEqual({ mode: 'cluster', cluster_nodes: 'n1:7000,n2:7001' });
  });

  test('TLS reveals CA, client cert and a masked client key; hostname check can be turned off', () => {
    const onChange = vi.fn();
    const { rerender } = render(<RedisForm sourceType="redis" value={{}} onChange={onChange} />);
    expect(screen.queryByLabelText('CA certificate (PEM, optional)')).not.toBeInTheDocument();
    fireEvent.click(screen.getByLabelText('Use TLS'));
    expect(lastArg(onChange)).toEqual({ tls: true });
    rerender(<RedisForm sourceType="redis" value={{ tls: true }} onChange={onChange} />);
    expect(screen.getByLabelText('Client private key (PEM, mutual TLS)')).toHaveAttribute('data-secret', 'true');
    fireEvent.change(screen.getByLabelText('Client certificate (PEM, mutual TLS)'), { target: { value: 'CERT' } });
    expect(lastArg(onChange)).toEqual({ tls: true, tls_client_cert: 'CERT' });
    fireEvent.click(screen.getByLabelText('Verify the certificate hostname'));
    expect(lastArg(onChange)).toEqual({ tls: true, tls_check_hostname: false });
  });

  test('key patterns and caps use the connector keys', () => {
    const onChange = vi.fn();
    render(<RedisForm sourceType="redis" value={{}} onChange={onChange} />);
    fireEvent.change(screen.getByLabelText('Key patterns'), { target: { value: 'kb:*' } });
    expect(lastArg(onChange)).toEqual({ key_patterns: 'kb:*' });
    fireEvent.change(screen.getByLabelText('Max keys per sync'), { target: { value: '50' } });
    expect(lastArg(onChange)).toEqual({ max_keys_per_sync: 50 });
  });
});
