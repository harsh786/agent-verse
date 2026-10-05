import { render, screen, fireEvent } from '@testing-library/react';
import { afterEach, describe, expect, test, vi } from 'vitest';
import { ObjectStorageForm } from './ObjectStorageForm';

function lastArg(fn: ReturnType<typeof vi.fn>): Record<string, unknown> {
  return fn.mock.calls.at(-1)![0] as Record<string, unknown>;
}

afterEach(() => vi.restoreAllMocks());

describe('ObjectStorageForm', () => {
  test('renders bucket, prefix, region fields for s3', () => {
    render(<ObjectStorageForm sourceType="s3" value={{}} onChange={vi.fn()} />);
    expect(screen.getByText('Bucket')).toBeInTheDocument();
    expect(screen.getByText('Key Prefix (optional)')).toBeInTheDocument();
    expect(screen.getByDisplayValue('us-east-1')).toBeInTheDocument();
  });

  test('always renders access key id and secret access key fields', () => {
    render(<ObjectStorageForm sourceType="gcs" value={{}} onChange={vi.fn()} />);
    expect(screen.getByText('Access Key ID')).toBeInTheDocument();
    expect(screen.getByText('Secret Access Key')).toBeInTheDocument();
    // gcs is not s3/minio/r2, so bucket fields should not render
    expect(screen.queryByText('Bucket')).not.toBeInTheDocument();
  });

  test('endpoint URL is required-looking for minio and optional for s3', () => {
    const { rerender } = render(<ObjectStorageForm sourceType="minio" value={{}} onChange={vi.fn()} />);
    expect(screen.getByText('Endpoint URL')).toBeInTheDocument();
    rerender(<ObjectStorageForm sourceType="s3" value={{}} onChange={vi.fn()} />);
    expect(screen.getByText('Endpoint URL (optional, S3-compatible stores)')).toBeInTheDocument();
    rerender(<ObjectStorageForm sourceType="gcs" value={{}} onChange={vi.fn()} />);
    expect(screen.queryByText(/Endpoint URL/)).not.toBeInTheDocument();
  });

  test('s3 keys are sent nested under credentials (what the connector reads), with a session token', () => {
    const onChange = vi.fn();
    const { rerender } = render(<ObjectStorageForm sourceType="minio" value={{ bucket: 'b' }} onChange={onChange} />);
    const input = (label: string) => screen.getByText(label).parentElement!.querySelector('input') as HTMLInputElement;
    fireEvent.change(input('Access Key ID'), { target: { value: 'AK' } });
    expect(lastArg(onChange)).toEqual({ bucket: 'b', credentials: { access_key_id: 'AK' } });
    rerender(<ObjectStorageForm sourceType="minio" value={lastArg(onChange)} onChange={onChange} />);
    fireEvent.change(input('Session Token (optional, temporary credentials)'), { target: { value: 'TOK' } });
    expect(lastArg(onChange)).toEqual({ bucket: 'b', credentials: { access_key_id: 'AK', session_token: 'TOK' } });
  });

  test('addressing style can be chosen', () => {
    const onChange = vi.fn();
    render(<ObjectStorageForm sourceType="s3" value={{}} onChange={onChange} />);
    fireEvent.change(screen.getByText('Addressing style').parentElement!.querySelector('select')!, { target: { value: 'virtual' } });
    expect(lastArg(onChange)).toEqual({ addressing_style: 'virtual' });
  });

  test('renders bucket fields for r2 too', () => {
    render(<ObjectStorageForm sourceType="r2" value={{}} onChange={vi.fn()} />);
    expect(screen.getByText('Bucket')).toBeInTheDocument();
    expect(screen.getByText('Region')).toBeInTheDocument();
  });

  test('typing bucket calls onChange with merged value', () => {
    const onChange = vi.fn();
    render(<ObjectStorageForm sourceType="s3" value={{ region: 'eu-west-1' }} onChange={onChange} />);
    fireEvent.change(screen.getByPlaceholderText('my-bucket'), { target: { value: 'acme-data' } });
    expect(lastArg(onChange)).toEqual({ region: 'eu-west-1', bucket: 'acme-data' });
  });

  test('typing secret access key calls onChange', () => {
    const onChange = vi.fn();
    render(<ObjectStorageForm sourceType="s3" value={{}} onChange={onChange} />);
    const secretInput = screen.getByText('Secret Access Key').parentElement?.querySelector('input');
    fireEvent.change(secretInput as HTMLInputElement, { target: { value: 'shh' } });
    expect(lastArg(onChange)).toEqual({ credentials: { secret_access_key: 'shh' } });
  });

  test('renders existing values', () => {
    render(<ObjectStorageForm sourceType="minio" value={{ bucket: 'b1', endpoint_url: 'http://minio:9000' }} onChange={vi.fn()} />);
    expect(screen.getByDisplayValue('b1')).toBeInTheDocument();
    expect(screen.getByDisplayValue('http://minio:9000')).toBeInTheDocument();
  });
});
