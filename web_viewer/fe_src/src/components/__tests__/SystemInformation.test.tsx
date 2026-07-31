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

describe('SystemInformation no admin access', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(forbiddenResponse()));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    MockSettingsPopover.mockClear();
  });

  it('always renders the gear button and passes allowAdmin down to SettingsPopover', async () => {
    render(
      <SystemInformation
        inverterData={{ serial: 'TEST' } as any}
        isSSEConnected={false}
        isOffline={false}
        onReconnect={() => {}}
      />
    );

    const gear = document.querySelector('.settings-button button');
    expect(gear).toBeInTheDocument();

    fireEvent.click(gear as Element);
    expect(await screen.findByTestId('settings-popover-mock')).toBeInTheDocument();

    await waitFor(() => {
      expect(MockSettingsPopover).toHaveBeenCalledWith(
        expect.objectContaining({ allowAdmin: false })
      );
    });
  });
});
