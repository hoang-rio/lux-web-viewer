import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { vi, describe, it, expect, beforeEach, afterEach } from 'vitest';
import SystemInformation from '../SystemInformation';

const { MockSettingsPopover } = vi.hoisted(() => ({
  MockSettingsPopover: vi.fn(() => <div data-testid="settings-popover-mock" />),
}));

// Mock i18next (SystemInformation also uses i18n.t)
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { t: (key: string) => key },
  }),
}));

// Mock Loading
vi.mock('../Loading', () => ({
  default: () => <div data-testid="loading">Loading</div>,
}));

// Mock the lazy-loaded SettingsPopover so we can assert the allowAdmin prop
vi.mock('../SettingsPopover', () => ({
  default: (props: any) => MockSettingsPopover(props),
}));

const forbiddenResponse = () => ({
  ok: false,
  status: 403,
  json: async () => ({ success: false }),
});

const allowedResponse = () => ({
  ok: true,
  status: 200,
  json: async () => ({ has_admin_access: true }),
});

describe('SystemInformation no admin access', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(forbiddenResponse()));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    MockSettingsPopover.mockClear();
  });

  it('hides the gear button when there is no admin access', async () => {
    render(
      <SystemInformation
        inverterData={{ serial: 'TEST' } as any}
        isSSEConnected={false}
        isOffline={false}
        onReconnect={() => {}}
      />
    );

    await waitFor(() => {
      expect(document.querySelector('.settings-button')).not.toBeInTheDocument();
    });
    expect(MockSettingsPopover).not.toHaveBeenCalled();
  });

  it('renders the gear button and passes allowAdmin down to SettingsPopover when admin access is granted', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(allowedResponse()));

    render(
      <SystemInformation
        inverterData={{ serial: 'TEST' } as any}
        isSSEConnected={false}
        isOffline={false}
        onReconnect={() => {}}
      />
    );

    const gear = await waitFor(() => {
      const el = document.querySelector('.settings-button button');
      expect(el).toBeInTheDocument();
      return el as Element;
    });

    fireEvent.click(gear);
    expect(await screen.findByTestId('settings-popover-mock')).toBeInTheDocument();

    await waitFor(() => {
      expect(MockSettingsPopover).toHaveBeenCalledWith(
        expect.objectContaining({ allowAdmin: true })
      );
    });
  });
});
