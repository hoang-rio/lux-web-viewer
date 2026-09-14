import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { vi, describe, it, expect, beforeEach, afterEach } from 'vitest';
import ModbusDashboard from '../ModbusDashboard';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { language: 'en' },
  }),
}));

vi.mock('../Loading', () => ({
  default: () => <div data-testid="loading">Loading</div>,
}));

const fixture = {
  status: { mode: 'dongle', available: true },
  categories: [
    {
      key: 'charge',
      name: 'Charge setting',
      items: [
        {
          key: 'buzzer',
          name: 'Buzzer beep',
          reg: 110,
          kind: 'toggle',
          danger: false,
          verify: false,
          bit: 7,
        },
        {
          key: 'charge_current',
          name: 'Charge current',
          reg: 72,
          kind: 'number',
          danger: false,
          verify: false,
          unit: 'A',
          min: 0,
          max: 100,
          scale: 0.1,
        },
        {
          key: 'ac_charge_type',
          name: 'AC charge type',
          reg: 120,
          kind: 'select',
          danger: false,
          verify: false,
          bit0: 1,
          bitwidth: 2,
          options: [
            { value: 0, label: 'Off' },
            { value: 1, label: 'Time' },
          ],
        },
        {
          key: 'grid_export_enable',
          name: 'Grid export enable',
          reg: 21,
          kind: 'toggle',
          danger: true,
          verify: false,
          bit: 15,
        },
      ],
    },
  ],
};

const makeFetch = (overrides: Record<string, { ok?: boolean; status?: number; body?: unknown }> = {}) => {
  return vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const override = Object.entries(overrides).find(([marker]) => url.includes(marker));
    if (override) {
      const cfg = override[1];
      return Promise.resolve({
        ok: cfg.ok !== false && (cfg.status === undefined || cfg.status < 400),
        status: cfg.status ?? 200,
        json: async () => cfg.body,
      });
    }
    if (url.includes('/modbus/registers')) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: async () => ({ ...fixture, language: 'en' }),
      });
    }
    if (url.includes('/modbus/read')) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: async () => ({
          success: true,
          values: { buzzer: 1, charge_current: 46.5, ac_charge_type: 0, grid_export_enable: 0 },
          status: fixture.status,
        }),
      });
    }
    if (url.includes('/modbus/write')) {
      const value = init?.body ? JSON.parse(String(init.body)).value : undefined;
      return Promise.resolve({
        ok: true,
        status: 200,
        json: async () => ({
          success: true,
          key: 'any',
          value: value ?? 0,
          status: fixture.status,
        }),
      });
    }
    return Promise.resolve({ ok: false, status: 404, json: async () => ({ success: false }) });
  });
};

describe('ModbusDashboard', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', makeFetch());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('renders category tabs and non-danger items, hiding danger items until advanced is enabled', async () => {
    render(<ModbusDashboard onClose={() => {}} />);

    expect(await screen.findByRole('button', { name: 'Charge setting' })).toBeInTheDocument();
    expect(screen.getByText('Buzzer beep')).toBeInTheDocument();
    expect(screen.getByText('Charge current')).toBeInTheDocument();
    expect(screen.getByText('AC charge type')).toBeInTheDocument();
    expect(screen.queryByText('Grid export enable')).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('checkbox', { name: 'modbus.advanced' }));
    expect(screen.getByText('Grid export enable')).toBeInTheDocument();
  });

  it('applies a number change via POST /modbus/write and shows success', async () => {
    const fetchMock = makeFetch();
    vi.stubGlobal('fetch', fetchMock);
    render(<ModbusDashboard onClose={() => {}} />);

    expect(await screen.findByText('Charge current')).toBeInTheDocument();

    const input = await screen.findByDisplayValue('46.5');
    await userEvent.clear(input);
    await userEvent.type(input, '50');

    await userEvent.click(screen.getAllByRole('button', { name: 'modbus.apply' })[0]);

    await waitFor(() => {
      expect(screen.getByText('modbus.writeSuccess')).toBeInTheDocument();
    });
    const writeCall = fetchMock.mock.calls.find(([url]) => String(url).includes('/modbus/write'));
    expect(writeCall).toBeTruthy();
    const [, init] = writeCall;
    expect(JSON.parse(init.body)).toEqual({ key: 'charge_current', value: 50, confirm: false });
  });

  it('requires confirmation modal for danger item writes', async () => {
    const fetchMock = makeFetch();
    vi.stubGlobal('fetch', fetchMock);
    const user = userEvent.setup();
    render(<ModbusDashboard onClose={() => {}} />);

    expect(await screen.findByText('Charge current')).toBeInTheDocument();
    await user.click(screen.getByRole('checkbox', { name: 'modbus.advanced' }));

    const dangerSwitch = await screen.findByRole('checkbox', { name: 'Grid export enable' });
    await user.click(dangerSwitch);

    expect(await screen.findByText('modbus.confirmDanger')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'modbus.yes' }));

    await waitFor(() => {
      expect(screen.getByText('modbus.writeSuccess')).toBeInTheDocument();
    });
    const writeCall = fetchMock.mock.calls.find(([url]) => String(url).includes('/modbus/write'));
    expect(writeCall).toBeTruthy();
    const [, init] = writeCall;
    expect(JSON.parse(init.body)).toEqual({ key: 'grid_export_enable', value: 1, confirm: true });
  });

  it('shows the no-access screen when registers endpoint denies access', async () => {
    vi.stubGlobal('fetch', makeFetch({ '/modbus/registers': { ok: false, status: 403, body: { success: false } } }));
    render(<ModbusDashboard onClose={() => {}} />);

    expect(await screen.findByText('modbus.noAccess')).toBeInTheDocument();
  });
});