import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { vi, describe, it, expect, beforeEach, afterEach } from 'vitest';
import { createRef } from 'react';
import type { INotificationData, IUpdateChart } from '../../Intefaces';
import HourlyChart from '../HourlyChart';

const { chartProps } = vi.hoisted(() => ({
  chartProps: { current: null as any },
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

vi.mock('../Loading', () => ({
  default: () => <div data-testid="loading">Loading</div>,
}));

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

const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
  const url = String(input);
  if (url.includes('/hourly-chart')) {
    return { json: async () => [chartRow] } as Response;
  }
  if (url.includes('/notification-history')) {
    return { json: async () => ({ notifications }) } as Response;
  }
  throw new Error(`unexpected fetch: ${url}`);
});

function notificationCalls() {
  return fetchMock.mock.calls.filter((c) => String(c[0]).includes('/notification-history'));
}

function points(): any[] {
  return chartProps.current?.options?.annotations?.points ?? [];
}

function firstDayUrl(): string {
  const calls = fetchMock.mock.calls.filter((c) => String(c[0]).includes('/hourly-chart'));
  return String(calls[0][0]);
}

describe('HourlyChart notification markers', () => {
  beforeEach(() => {
    chartProps.current = null;
    notifications = [];
    fetchMock.mockClear();
    vi.stubGlobal('fetch', fetchMock);
    vi.stubGlobal(
      'matchMedia',
      vi.fn().mockImplementation((query: string) => ({
        matches: false,
        media: query,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      }))
    );
    window.matchMedia = vi.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })) as any;
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('requests both endpoints with the selected date', async () => {
    render(<HourlyChart className="test" />);

    await waitFor(() => expect(notificationCalls()).toHaveLength(1));
    expect(String(notificationCalls()[0][0])).toContain(
      `/notification-history?date=${todayStr}`
    );
    expect(firstDayUrl()).toContain(`/hourly-chart?date=${todayStr}`);
  });

  it('renders one annotation per notification', async () => {
    notifications = [
      notification({ id: 1, notified_at: '2026-09-30 10:15:00' }),
      notification({ id: 2, notified_at: '2026-09-30 12:30:00' }),
    ];
    render(<HourlyChart className="test" />);

    await waitFor(() => expect(screen.getByTestId('chart')).toBeInTheDocument());
    await waitFor(() => expect(points()).toHaveLength(2));
  });

  it('pins annotations to the plot top by omitting y', async () => {
    notifications = [notification({ notified_at: '2026-09-30 10:15:00' })];
    render(<HourlyChart className="test" />);

    await waitFor(() => expect(points()).toHaveLength(1));
    expect(points()[0].y).toBeUndefined();
    expect(typeof points()[0].x).toBe('number');
  });

  it('merges notifications that fall in the same minute', async () => {
    notifications = [
      notification({ id: 1, title: 'A', body: 'first', notified_at: '2026-09-30 10:15:00' }),
      notification({ id: 2, title: 'B', body: 'second', notified_at: '2026-09-30 10:15:45' }),
      notification({ id: 3, title: 'C', body: 'third', notified_at: '2026-09-30 10:16:00' }),
    ];
    render(<HourlyChart className="test" />);

    await waitFor(() => expect(points()).toHaveLength(2));
    const tooltipHtml = points()[0].tooltip.formatter({ annotation: points()[0] });
    expect(tooltipHtml).toContain('first');
    expect(tooltipHtml).toContain('second');
    // The next minute is a separate marker
    expect(points()[1].tooltip.formatter({ annotation: points()[1] })).toContain('third');
  });

  it('escapes notification content in the tooltip markup', async () => {
    notifications = [
      notification({
        title: '<img src=x onerror=alert(1)>',
        body: "<script>alert('xss')</script>",
        notified_at: '2026-09-30 10:15:00',
      }),
    ];
    render(<HourlyChart className="test" />);

    await waitFor(() => expect(points()).toHaveLength(1));
    const tooltipHtml = points()[0].tooltip.formatter({ annotation: points()[0] });
    expect(tooltipHtml).not.toContain('<img');
    expect(tooltipHtml).not.toContain('<script>');
    expect(tooltipHtml).toContain('&lt;img');
    expect(tooltipHtml).toContain('&lt;script&gt;');
  });

  it('includes the notified-at label and local clock in the tooltip', async () => {
    notifications = [notification({ notified_at: '2026-09-30 10:15:00' })];
    render(<HourlyChart className="test" />);

    await waitFor(() => expect(points()).toHaveLength(1));
    const tooltipHtml = points()[0].tooltip.formatter({ annotation: points()[0] });
    expect(tooltipHtml).toContain('hourlyChart.notifiedAt 10:15');
  });

  it('skips notifications with an unparseable timestamp', async () => {
    notifications = [
      notification({ id: 1, notified_at: 'not-a-date' }),
      notification({ id: 2, notified_at: '2026-09-30 10:15:00' }),
    ];
    render(<HourlyChart className="test" />);

    await waitFor(() => expect(screen.getByTestId('chart')).toBeInTheDocument());
    await waitFor(() => expect(points()).toHaveLength(1));
  });

  it('re-fetches notifications when refreshNotifications is called', async () => {
    const ref = createRef<IUpdateChart>();
    render(<HourlyChart className="test" ref={ref} />);

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
    render(<HourlyChart className="test" ref={ref} />);

    await waitFor(() => expect(notificationCalls()).toHaveLength(1));

    const pastDate = formatLocalDate(new Date(Date.now() - 5 * 86400000));
    const dateInput = screen.getByDisplayValue(todayStr);
    await act(async () => {
      fireEvent.change(dateInput, { target: { value: pastDate } });
    });

    const afterChange = notificationCalls().length;
    expect(String(notificationCalls()[afterChange - 1][0])).toContain(
      `/notification-history?date=${pastDate}`
    );

    fetchMock.mockClear();
    await act(async () => {
      ref.current?.refreshNotifications();
    });
    expect(notificationCalls()).toHaveLength(0);
  });

  it('coalesces a burst of refresh calls into a single request', async () => {
    const ref = createRef<IUpdateChart>();
    render(<HourlyChart className="test" ref={ref} />);

    await waitFor(() => expect(notificationCalls()).toHaveLength(1));
    fetchMock.mockClear();

    await act(async () => {
      ref.current?.refreshNotifications();
      ref.current?.refreshNotifications();
      ref.current?.refreshNotifications();
    });

    await waitFor(() => expect(notificationCalls()).toHaveLength(1));
  });
});
