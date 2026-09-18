import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { vi, describe, it, expect, beforeEach, afterEach } from 'vitest';
import ModbusDashboard from '../ModbusDashboard';

// Keep `t` module-stable: ModbusDashboard memoizes `fetchAll` on `t`, so a
// fresh `t` every render would re-run the effect forever.
vi.mock('react-i18next', () => {
  const t = (key: string, options?: { defaultValue?: string }) =>
    options && options.defaultValue !== undefined ? options.defaultValue : key;
  return { useTranslation: () => ({ t }) };
});

vi.mock('../Loading', () => ({
  default: () => <div data-testid="loading">Loading</div>,
}));

// apiFetch (via ../utils/fetchUtil) imports the i18n instance for error
// messages; give it a passthrough t so the real i18n bootstrap is not needed.
vi.mock('../../i18n', () => {
  const t = (key: string, options?: { defaultValue?: string }) =>
    options && options.defaultValue !== undefined ? options.defaultValue : key;
  return { default: { t } };
});

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

const readBody = {
  success: true,
  values: { buzzer: 1, charge_current: 46.5, ac_charge_type: 0, grid_export_enable: 0 },
  status: fixture.status,
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
        json: async () => ({ ...fixture }),
      });
    }
    if (url.includes('/modbus/read')) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: async () => readBody,
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
    const user = userEvent.setup();
    render(<ModbusDashboard onClose={() => {}} />);

    const heading = await screen.findByRole('button', { name: /modbus\.cat\.charge/ });
    expect(heading).toBeInTheDocument();
    expect(heading).toHaveAttribute('aria-expanded', 'false');
    await user.click(heading);

    expect(screen.getByText('modbus.reg.buzzer')).toBeInTheDocument();
    expect(screen.getByText('modbus.reg.charge_current')).toBeInTheDocument();
    expect(screen.getByText('modbus.reg.ac_charge_type')).toBeInTheDocument();
    expect(screen.queryByText('modbus.reg.grid_export_enable')).not.toBeInTheDocument();

    await user.click(screen.getByRole('checkbox', { name: 'modbus.advanced' }));
    expect(screen.getByText('modbus.reg.grid_export_enable')).toBeInTheDocument();
  });

  it('applies a number change via POST /modbus/write and shows success', async () => {
    const fetchMock = makeFetch();
    vi.stubGlobal('fetch', fetchMock);
    const user = userEvent.setup();
    render(<ModbusDashboard onClose={() => {}} />);

    const heading = await screen.findByRole('button', { name: /modbus\.cat\.charge/ });
    await user.click(heading);

    const input = await screen.findByDisplayValue('46.5');
    await user.clear(input);
    await user.type(input, '50');

    await user.click(screen.getAllByRole('button', { name: 'modbus.apply' })[0]);

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

    const heading = await screen.findByRole('button', { name: /modbus\.cat\.charge/ });
    await user.click(heading);
    await user.click(screen.getByRole('checkbox', { name: 'modbus.advanced' }));

    const dangerSwitch = await screen.findByRole('checkbox', { name: 'modbus.reg.grid_export_enable' });
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

  it('scopes reads and writes to the selected inverter when inverterId is provided', async () => {
    const fetchMock = makeFetch();
    vi.stubGlobal('fetch', fetchMock);
    const user = userEvent.setup();
    render(<ModbusDashboard onClose={() => {}} inverterId="3a7b1c2d-0000-0000-0000-000000000000" />);

    const heading = await screen.findByRole('button', { name: /modbus\.cat\.charge/ });
    await user.click(heading);

    const readCall = fetchMock.mock.calls.find(([url]) => String(url).includes('/modbus/read'));
    expect(readCall).toBeTruthy();
    expect(String(readCall[0])).toContain('inverter_id=3a7b1c2d-0000-0000-0000-000000000000');

    const input = await screen.findByDisplayValue('46.5');
    await user.clear(input);
    await user.type(input, '50');
    await user.click(screen.getAllByRole('button', { name: 'modbus.apply' })[0]);

    await waitFor(() => {
      expect(screen.getByText('modbus.writeSuccess')).toBeInTheDocument();
    });
    const writeCall = fetchMock.mock.calls.find(([url]) => String(url).includes('/modbus/write'));
    expect(writeCall).toBeTruthy();
    const [, init] = writeCall;
    expect(JSON.parse(init.body)).toEqual({
      key: 'charge_current',
      value: 50,
      confirm: false,
      inverter_id: '3a7b1c2d-0000-0000-0000-000000000000',
    });
  });

  it('shows the no-access screen when registers endpoint denies access', async () => {
    vi.stubGlobal('fetch', makeFetch({ '/modbus/registers': { ok: false, status: 403, body: { success: false } } }));
    render(<ModbusDashboard onClose={() => {}} />);

    expect(await screen.findByText('modbus.noAccess')).toBeInTheDocument();
  });

  it('shows a content loading state while a refresh request is in flight', async () => {
    const user = userEvent.setup();
    const readResolvers: Array<(v: unknown) => void> = [];
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes('/modbus/read')) {
        // Hold the read request so the refresh stays in-flight.
        return new Promise((resolve) => {
          readResolvers.push(resolve);
        });
      }
      return makeFetch()(input, init);
    });
    vi.stubGlobal('fetch', fetchMock);
    render(<ModbusDashboard onClose={() => {}} />);

    const heading = await screen.findByRole('button', { name: /modbus\.cat\.charge/ });
    await user.click(heading);
    expect(screen.getByText('modbus.reg.charge_current')).toBeInTheDocument();

    await user.click(screen.getByTitle('modbus.refresh'));

    expect(await screen.findByRole('status')).toBeInTheDocument();
    expect(screen.getByText('modbus.loading')).toBeInTheDocument();

    // The refresh triggers a second /modbus/read request (registers resolve first).
    await waitFor(() => {
      expect(readResolvers.length).toBeGreaterThan(0);
    });
    const response = {
      ok: true,
      status: 200,
      json: async () => readBody,
    };
    readResolvers.forEach((resolve) => resolve(response));

    await waitFor(() => {
      expect(screen.queryByRole('status')).not.toBeInTheDocument();
    });
    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/modbus/read'))).toBe(true);
    });
  });
});