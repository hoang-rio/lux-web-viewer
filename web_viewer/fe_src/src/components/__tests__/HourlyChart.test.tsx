import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { vi, describe, it, expect, beforeEach, afterEach } from 'vitest';
import { createRef } from 'react';
import type { INotificationData, IUpdateChart } from '../../Intefaces';
import HourlyChart from '../HourlyChart';

const { chartProps, apiGetJsonOrThrow } = vi.hoisted(() => ({
  chartProps: { current: null as any },
  apiGetJsonOrThrow: vi.fn(),
}));

vi.mock('react-apexcharts', () => ({
  default: (props: any) => {
    chartProps.current = props;
    const count = props.options?.annotations?.points?.length ?? 0;
    return <div data-testid="chart" data-annotation-count={count} />;
  },
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { t: (key: string) => key },
  }),
}));

vi.mock('../../utils/fetchUtil', () => ({
  apiGetJsonOrThrow,
}));

vi.mock('../Loading', () => ({
  default: () => <div data-testid="loading">Loading</div>,
}));

const AUTH_TOKEN = 'test-access-token';
const INVERTER_ID = '11111111-1111-1111-1111-111111111111';

function formatLocalDate(date: Date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

const todayStr = formatLocalDate(new Date());

function notification(overrides: Partial<INotificationData> = {}): INotificationData {
  return {
    id: 1,
    title: 'High power',
    body: 'Export above limit',
    notified_at: '2026-09-30 10:15:00',
    read: 0,
    inverter_id: INVERTER_ID,
    ...overrides,
  };
}

const chartRow = [
  '2026-09-30 10:00:00',
  '2026-09-30 10:00:00',
  100,
  50,
  -20,
  30,
  80,
];

let notifications: INotificationData[] = [];

function points(): any[] {
  return chartProps.current?.options?.annotations?.points ?? [];
}

function callsTo(pathFragment: string) {
  return apiGetJsonOrThrow.mock.calls.filter((c) => String(c[0]).includes(pathFragment));
}

function notificationCalls() {
  return callsTo('/notification-history');
}

describe('HourlyChart notification markers (multi-tenant)', () => {
  beforeEach(() => {
    chartProps.current = null;
    notifications = [];
    apiGetJsonOrThrow.mockReset();
    apiGetJsonOrThrow.mockImplementation(async (path: string) => {
      if (path.includes('/hourly-chart')) {
        return [chartRow];
      }
      if (path.includes('/notification-history')) {
        return { notifications };
      }
      throw new Error(`unexpected call: ${path}`);
    });
    const mq = vi.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }));
    vi.stubGlobal('matchMedia', mq);
    window.matchMedia = mq as any;
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('requests both endpoints with date and inverter_id', async () => {
    render(
      <HourlyChart className="test" selectedInverterId={INVERTER_ID} authToken={AUTH_TOKEN} />
    );

    await waitFor(() => expect(notificationCalls()).toHaveLength(1));
    const path = String(notificationCalls()[0][0]);
    expect(path).toContain('/notification-history?');
    expect(path).toContain(`date=${todayStr}`);
    expect(path).toContain(`inverter_id=${INVERTER_ID}`);

    const options = notificationCalls()[0][1];
    expect(options).toEqual({ withAuth: true });

    expect(String(callsTo('/hourly-chart')[0][0])).toContain(`inverter_id=${INVERTER_ID}`);
  });

  it('omits inverter_id when no inverter is selected yet', async () => {
    render(<HourlyChart className="test" />);

    await waitFor(() => expect(notificationCalls()).toHaveLength(1));
    expect(String(notificationCalls()[0][0])).not.toContain('inverter_id');
    expect(notificationCalls()[0][1]).toEqual({ withAuth: false });
  });

  it('renders one annotation per notification', async () => {
    notifications = [
      notification({ id: 1, notified_at: '2026-09-30 10:15:00' }),
      notification({ id: 2, notified_at: '2026-09-30 12:30:00' }),
    ];
    render(<HourlyChart className="test" selectedInverterId={INVERTER_ID} authToken={AUTH_TOKEN} />);

    await waitFor(() => expect(screen.getByTestId('chart')).toBeInTheDocument());
    await waitFor(() => expect(points()).toHaveLength(2));
    expect(points()[0].y).toBeUndefined();
  });

  it('merges notifications that fall in the same minute', async () => {
    notifications = [
      notification({ id: 1, title: 'A', body: 'first', notified_at: '2026-09-30 10:15:00' }),
      notification({ id: 2, title: 'B', body: 'second', notified_at: '2026-09-30 10:15:45' }),
      notification({ id: 3, title: 'C', body: 'third', notified_at: '2026-09-30 10:16:00' }),
    ];
    render(<HourlyChart className="test" selectedInverterId={INVERTER_ID} authToken={AUTH_TOKEN} />);

    await waitFor(() => expect(points()).toHaveLength(2));
    const first = points()[0].tooltip.formatter({ annotation: points()[0] });
    expect(first).toContain('first');
    expect(first).toContain('second');
  });

  it('escapes notification content in the tooltip markup', async () => {
    notifications = [
      notification({
        title: '<img src=x onerror=alert(1)>',
        body: "<script>alert('xss')</script>",
        notified_at: '2026-09-30 10:15:00',
      }),
    ];
    render(<HourlyChart className="test" selectedInverterId={INVERTER_ID} authToken={AUTH_TOKEN} />);

    await waitFor(() => expect(points()).toHaveLength(1));
    const html = points()[0].tooltip.formatter({ annotation: points()[0] });
    expect(html).not.toContain('<img');
    expect(html).not.toContain('<script>');
    expect(html).toContain('&lt;img');
    expect(html).toContain('&lt;script&gt;');
  });

  it('re-fetches notifications when refreshNotifications is called', async () => {
    const ref = createRef<IUpdateChart>();
    render(
      <HourlyChart className="test" ref={ref} selectedInverterId={INVERTER_ID} authToken={AUTH_TOKEN} />
    );

    await waitFor(() => expect(notificationCalls()).toHaveLength(1));

    notifications = [notification({ notified_at: '2026-09-30 10:15:00' })];
    await act(async () => {
      ref.current?.refreshNotifications();
    });

    await waitFor(() => expect(notificationCalls()).toHaveLength(2));
    await waitFor(() => expect(points()).toHaveLength(1));
  });

  it('does not re-fetch when a past date is selected', async () => {
    const ref = createRef<IUpdateChart>();
    render(
      <HourlyChart className="test" ref={ref} selectedInverterId={INVERTER_ID} authToken={AUTH_TOKEN} />
    );

    await waitFor(() => expect(notificationCalls()).toHaveLength(1));

    const pastDate = formatLocalDate(new Date(Date.now() - 5 * 86400000));
    await act(async () => {
      fireEvent.change(screen.getByDisplayValue(todayStr), { target: { value: pastDate } });
    });

    expect(String(notificationCalls().at(-1)?.[0])).toContain(`date=${pastDate}`);

    apiGetJsonOrThrow.mockClear();
    await act(async () => {
      ref.current?.refreshNotifications();
    });
    expect(notificationCalls()).toHaveLength(0);
  });

  it('coalesces a burst of refresh calls into a single request', async () => {
    const ref = createRef<IUpdateChart>();
    render(
      <HourlyChart className="test" ref={ref} selectedInverterId={INVERTER_ID} authToken={AUTH_TOKEN} />
    );

    await waitFor(() => expect(notificationCalls()).toHaveLength(1));
    apiGetJsonOrThrow.mockClear();

    await act(async () => {
      ref.current?.refreshNotifications();
      ref.current?.refreshNotifications();
      ref.current?.refreshNotifications();
    });

    await waitFor(() => expect(notificationCalls()).toHaveLength(1));
  });

  it('clears markers when the notification request fails', async () => {
    apiGetJsonOrThrow.mockImplementation(async (path: string) => {
      if (path.includes('/hourly-chart')) {
        return [chartRow];
      }
      throw new Error('boom');
    });
    const ref = createRef<IUpdateChart>();
    render(
      <HourlyChart className="test" ref={ref} selectedInverterId={INVERTER_ID} authToken={AUTH_TOKEN} />
    );

    await waitFor(() => expect(screen.getByTestId('chart')).toBeInTheDocument());
    await waitFor(() => expect(points()).toHaveLength(0));
  });
});
