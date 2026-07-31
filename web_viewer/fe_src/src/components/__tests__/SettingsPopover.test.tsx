import { render, screen } from '@testing-library/react';
import { vi, describe, it, expect, beforeEach, afterEach } from 'vitest';
import SettingsPopover from '../SettingsPopover';

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

describe('SettingsPopover no admin access', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(forbiddenResponse()));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('renders the no-permission message instead of the form when /settings returns 403', async () => {
    render(<SettingsPopover allowAdmin onClose={() => {}} onOpenTriggers={() => {}} />);

    expect(await screen.findByText('settings.noAdminPermission')).toBeInTheDocument();
    expect(screen.getByText('settings.noAdminPermissionHint')).toBeInTheDocument();
    expect(screen.queryByText('settings.batterySection')).not.toBeInTheDocument();
    expect(screen.queryByText('settings.authSection')).not.toBeInTheDocument();
    expect(screen.queryByText('settings.save')).not.toBeInTheDocument();
  });

  it('renders the no-permission message when allowAdmin is false', async () => {
    render(<SettingsPopover allowAdmin={false} onClose={() => {}} onOpenTriggers={() => {}} />);

    expect(await screen.findByText('settings.noAdminPermission')).toBeInTheDocument();
    expect(screen.queryByText('settings.batterySection')).not.toBeInTheDocument();
    expect(screen.queryByText('settings.save')).not.toBeInTheDocument();
  });
});
