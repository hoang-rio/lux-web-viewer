import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import TriggerForm from '../trigger-dashboard/TriggerForm';
import { createEmptyTrigger } from '../trigger-dashboard/constants';
import type { ITrigger } from '../../Intefaces';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

function makeTrigger(actionType: string): ITrigger {
  return {
    ...createEmptyTrigger(),
    name: 'Test trigger',
    actions: [{ action_type: actionType, device_id: 'dev1', dps_key: '1', params: {} }],
    action_type: actionType,
    action_device_id: 'dev1',
  };
}

function renderForm(trigger: ITrigger, onSave = vi.fn()) {
  render(
    <TriggerForm
      trigger={trigger}
      devices={[{ id: 'dev1', name: 'Relay' } as never]}
      deviceMappings={{}}
      castConfigured={false}
      onSave={onSave}
      onCancel={() => {}}
    />,
  );
  return { onSave };
}

describe('TriggerForm execute failed action', () => {
  it('is hidden when the main action is not a Tuya device control', () => {
    renderForm(makeTrigger('notification'));

    expect(screen.queryByText('triggers.executeFailedSection')).not.toBeInTheDocument();
  });

  it('offers only notification and retry, and shows retry count only for retry', () => {
    renderForm(makeTrigger('tuya_on'));

    const section = screen.getByText('triggers.executeFailedSection').closest('.form-section') as HTMLElement;
    expect(section).toBeTruthy();
    const typeSelect = within(section).getByRole('combobox');
    expect(within(typeSelect).getAllByRole('option').map((o) => o.getAttribute('value'))).toEqual([
      'notification',
      'retry',
    ]);
    expect(within(section).getByText('triggers.notifTitle')).toBeInTheDocument();
    expect(within(section).queryByText('triggers.retryCount')).not.toBeInTheDocument();

    fireEvent.change(typeSelect, { target: { value: 'retry' } });

    expect(within(section).getByText('triggers.retryCount')).toBeInTheDocument();
    expect(within(section).queryByText('triggers.notifTitle')).not.toBeInTheDocument();
  });

  it('saves a single failure action with the configured retry count', () => {
    const { onSave } = renderForm(makeTrigger('tuya_set'));

    const section = screen.getByText('triggers.executeFailedSection').closest('.form-section') as HTMLElement;
    fireEvent.change(within(section).getByRole('combobox'), { target: { value: 'retry' } });
    fireEvent.change(within(section).getByRole('spinbutton'), { target: { value: '5' } });
    fireEvent.click(screen.getByText('triggers.save'));

    expect(onSave).toHaveBeenCalledWith(expect.objectContaining({
      action_params: expect.objectContaining({
        execute_failed: expect.objectContaining({ action_type: 'retry', retry_count: 5 }),
      }),
    }));
  });

  it('clears the failure action when the main action changes away from Tuya', () => {
    const { onSave } = renderForm(makeTrigger('tuya_on'));

    const mainRow = document.querySelector('.action-row') as HTMLElement;
    fireEvent.change(within(mainRow).getAllByRole('combobox')[0], { target: { value: 'notification' } });
    fireEvent.click(screen.getByText('triggers.save'));

    expect(screen.queryByText('triggers.executeFailedSection')).not.toBeInTheDocument();
    expect(onSave).toHaveBeenCalledWith(expect.objectContaining({
      action_params: expect.objectContaining({ execute_failed: null }),
    }));
  });
});
