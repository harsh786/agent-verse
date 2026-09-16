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

  test('renders endpoint URL field only for minio', () => {
    const { rerender } = render(<ObjectStorageForm sourceType="minio" value={{}} onChange={vi.fn()} />);
    expect(screen.getByText('Endpoint URL')).toBeInTheDocument();
    rerender(<ObjectStorageForm sourceType="s3" value={{}} onChange={vi.fn()} />);
    expect(screen.queryByText('Endpoint URL')).not.toBeInTheDocument();
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
    expect(lastArg(onChange)).toEqual({ secret_access_key: 'shh' });
  });

  test('renders existing values', () => {
    render(<ObjectStorageForm sourceType="minio" value={{ bucket: 'b1', endpoint_url: 'http://minio:9000' }} onChange={vi.fn()} />);
    expect(screen.getByDisplayValue('b1')).toBeInTheDocument();
    expect(screen.getByDisplayValue('http://minio:9000')).toBeInTheDocument();
  });
});
