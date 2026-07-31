import { render, screen } from '@testing-library/react';
import { vi, describe, it, expect, beforeEach, afterEach } from 'vitest';
import TriggerDashboard from '../TriggerDashboard';

// Mock i18next
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
  }),
}));

// Mock Loading
vi.mock('../Loading', () => ({
  default: () => <div data-testid="loading">Loading</div>,
}));

const forbiddenResponse = () => ({
  ok: false,
  status: 403,
  json: async () => ({ success: false }),
});

describe('TriggerDashboard no admin access', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(forbiddenResponse()));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('renders the no-permission message instead of the tab lists when fetches return 403', async () => {
    render(<TriggerDashboard onClose={() => {}} />);

    expect(await screen.findByText('triggers.noAddPermission')).toBeInTheDocument();
    expect(screen.getByText('triggers.noAddPermissionHint')).toBeInTheDocument();
    expect(screen.queryByText('triggers.tabTriggers')).not.toBeInTheDocument();
    expect(screen.queryByText('triggers.tabDevices')).not.toBeInTheDocument();
    expect(screen.queryByText('triggers.addTrigger')).not.toBeInTheDocument();
    expect(screen.queryByText('triggers.scanForDevices')).not.toBeInTheDocument();
    expect(screen.queryByText('triggers.noTriggers')).not.toBeInTheDocument();
    expect(screen.queryByText('triggers.noDevices')).not.toBeInTheDocument();
  });
});
