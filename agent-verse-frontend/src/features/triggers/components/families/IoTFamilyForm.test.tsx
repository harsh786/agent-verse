import { render, screen, fireEvent } from '@testing-library/react';
import { afterEach, describe, expect, test, vi } from 'vitest';
import { IoTFamilyForm } from './IoTFamilyForm';

/** Grab the object the component most recently handed to onChange. */
function lastArg(fn: ReturnType<typeof vi.fn>): Record<string, unknown> {
  return fn.mock.calls.at(-1)![0] as Record<string, unknown>;
}

afterEach(() => vi.restoreAllMocks());

describe('IoTFamilyForm', () => {
  test('renders nothing for an unrelated trigger type', () => {
    const { container } = render(
      <IoTFamilyForm triggerType="cron" value={{}} onChange={vi.fn()} />,
    );
    expect(container.querySelector('.space-y-4')?.children.length).toBe(0);
  });

  describe('mqtt', () => {
    test('renders broker URL, topic, and QoS fields with defaults', () => {
      render(<IoTFamilyForm triggerType="mqtt" value={{}} onChange={vi.fn()} />);
      expect(screen.getByPlaceholderText('mqtt://broker.example.com:1883')).toHaveValue('');
      expect(screen.getByPlaceholderText('sensors/+/temperature')).toHaveValue('');
      expect(screen.getByRole('combobox')).toHaveValue('0');
    });

    test('editing broker URL merges into value', () => {
      const onChange = vi.fn();
      render(<IoTFamilyForm triggerType="mqtt" value={{ description: 'x' }} onChange={onChange} />);
      fireEvent.change(screen.getByPlaceholderText('mqtt://broker.example.com:1883'), {
        target: { value: 'mqtt://host:1883' },
      });
      expect(lastArg(onChange)).toEqual({ description: 'x', mqtt_broker_url: 'mqtt://host:1883' });
    });

    test('editing topic pattern merges into value', () => {
      const onChange = vi.fn();
      render(<IoTFamilyForm triggerType="mqtt" value={{}} onChange={onChange} />);
      fireEvent.change(screen.getByPlaceholderText('sensors/+/temperature'), {
        target: { value: 'sensors/#' },
      });
      expect(lastArg(onChange)).toEqual({ mqtt_topic: 'sensors/#' });
    });

    test('changing QoS coerces to a number', () => {
      const onChange = vi.fn();
      render(<IoTFamilyForm triggerType="mqtt" value={{}} onChange={onChange} />);
      fireEvent.change(screen.getByRole('combobox'), { target: { value: '2' } });
      expect(lastArg(onChange)).toEqual({ mqtt_qos: 2 });
    });

    test('shows a passed QoS value', () => {
      render(<IoTFamilyForm triggerType="mqtt" value={{ mqtt_qos: 1 }} onChange={vi.fn()} />);
      expect(screen.getByRole('combobox')).toHaveValue('1');
    });
  });

  describe('geofence', () => {
    test('defaults the action select to "enter"', () => {
      render(<IoTFamilyForm triggerType="geofence" value={{}} onChange={vi.fn()} />);
      expect(screen.getByRole('combobox')).toHaveValue('enter');
    });

    test('changing the geofence action merges into value', () => {
      const onChange = vi.fn();
      render(<IoTFamilyForm triggerType="geofence" value={{}} onChange={onChange} />);
      fireEvent.change(screen.getByRole('combobox'), { target: { value: 'both' } });
      expect(lastArg(onChange)).toEqual({ geofence_action: 'both' });
    });

    test('renders an empty polygon textarea by default', () => {
      render(<IoTFamilyForm triggerType="geofence" value={{}} onChange={vi.fn()} />);
      expect(screen.getByPlaceholderText(/51.5, -0.1/)).toHaveValue('');
    });

    test('stringifies an existing polygon value into the textarea', () => {
      render(
        <IoTFamilyForm
          triggerType="geofence"
          value={{ geofence_polygon: [[1, 2], [3, 4]] }}
          onChange={vi.fn()}
        />,
      );
      expect(screen.getByPlaceholderText(/51.5, -0.1/)).toHaveValue('[[1,2],[3,4]]');
    });

    test('parses valid JSON typed into the polygon textarea', () => {
      const onChange = vi.fn();
      render(<IoTFamilyForm triggerType="geofence" value={{}} onChange={onChange} />);
      fireEvent.change(screen.getByPlaceholderText(/51.5, -0.1/), {
        target: { value: '[[1,2]]' },
      });
      expect(lastArg(onChange)).toEqual({ geofence_polygon: [[1, 2]] });
    });

    test('falls back to the raw string when the polygon textarea has invalid JSON', () => {
      const onChange = vi.fn();
      render(<IoTFamilyForm triggerType="geofence" value={{}} onChange={onChange} />);
      fireEvent.change(screen.getByPlaceholderText(/51.5, -0.1/), {
        target: { value: 'not json' },
      });
      expect(lastArg(onChange)).toEqual({ geofence_polygon: 'not json' });
    });
  });

  describe('sensor_threshold', () => {
    test('renders device id, metric, threshold, and comparison with defaults', () => {
      render(<IoTFamilyForm triggerType="sensor_threshold" value={{}} onChange={vi.fn()} />);
      expect(screen.getByPlaceholderText('device-001')).toHaveValue('');
      expect(screen.getByPlaceholderText('temperature')).toHaveValue('');
      expect(screen.getByDisplayValue('80')).toBeInTheDocument();
      expect(screen.getByRole('combobox')).toHaveValue('>');
    });

    test('editing device id merges into value', () => {
      const onChange = vi.fn();
      render(<IoTFamilyForm triggerType="sensor_threshold" value={{}} onChange={onChange} />);
      fireEvent.change(screen.getByPlaceholderText('device-001'), { target: { value: 'dev-42' } });
      expect(lastArg(onChange)).toEqual({ sensor_device_id: 'dev-42' });
    });

    test('editing metric name merges into value', () => {
      const onChange = vi.fn();
      render(<IoTFamilyForm triggerType="sensor_threshold" value={{}} onChange={onChange} />);
      fireEvent.change(screen.getByPlaceholderText('temperature'), { target: { value: 'humidity' } });
      expect(lastArg(onChange)).toEqual({ sensor_metric: 'humidity' });
    });

    test('editing threshold coerces to a number', () => {
      const onChange = vi.fn();
      render(<IoTFamilyForm triggerType="sensor_threshold" value={{}} onChange={onChange} />);
      fireEvent.change(screen.getByDisplayValue('80'), { target: { value: '95.5' } });
      expect(lastArg(onChange)).toEqual({ sensor_threshold: 95.5 });
    });

    test('changing comparison merges into value', () => {
      const onChange = vi.fn();
      render(<IoTFamilyForm triggerType="sensor_threshold" value={{}} onChange={onChange} />);
      fireEvent.change(screen.getByRole('combobox'), { target: { value: '<=' } });
      expect(lastArg(onChange)).toEqual({ sensor_comparison: '<=' });
    });

    test('shows a passed threshold and comparison value', () => {
      render(
        <IoTFamilyForm
          triggerType="sensor_threshold"
          value={{ sensor_threshold: 12, sensor_comparison: '==' }}
          onChange={vi.fn()}
        />,
      );
      expect(screen.getByDisplayValue('12')).toBeInTheDocument();
      expect(screen.getByRole('combobox')).toHaveValue('==');
    });
  });
});
