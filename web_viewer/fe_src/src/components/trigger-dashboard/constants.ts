import { IDeviceMapping, ITrigger, ITriggerAction } from '../../Intefaces';

export const INVERTER_FIELDS = [
  { value: 'soc', label: 'triggers.condSOC' },
  { value: 'p_pv', label: 'triggers.condPVPower' },
  { value: 'p_discharge', label: 'triggers.condDischarge' },
  { value: 'p_charge', label: 'triggers.condCharge' },
  { value: 'fac', label: 'triggers.condGridFreq' },
  { value: 'p_eps', label: 'triggers.condEPS' },
  { value: 'p_to_grid', label: 'triggers.condToGrid' },
  { value: 'p_to_user', label: 'triggers.condToUser' },
  { value: 'v_bat', label: 'triggers.condBatVoltage' },
];

export const CONDITION_OPS = ['>', '<', '>=', '<=', '==', '!='];

export const DAY_KEYS = ['triggers.dayMon', 'triggers.dayTue', 'triggers.dayWed', 'triggers.dayThu', 'triggers.dayFri', 'triggers.daySat', 'triggers.daySun'];

export function getDeviceDpsList(deviceId: string | null | undefined, mappings: Record<string, IDeviceMapping>): [string, { code: string; type: string; values?: { min?: number; max?: number; range?: string[] } }][] {
  if (!deviceId || !mappings[deviceId]) return [];
  return Object.entries(mappings[deviceId].mapping);
}

export function getActionTypeOptions(castConfigured: boolean): { value: string; label: string }[] {
  const options = [
    { value: 'notification', label: 'triggers.actionNotification' },
    { value: 'tuya_on', label: 'triggers.actionTurnOn' },
    { value: 'tuya_off', label: 'triggers.actionTurnOff' },
    { value: 'tuya_set', label: 'triggers.actionSetValue' },
  ];
  if (castConfigured) {
    options.splice(1, 0, { value: 'play_audio', label: 'triggers.actionPlayAudio' });
  }
  return options;
}

export function formatDays(daysStr: string | null, t: (key: string) => string): string {
  if (!daysStr) return '';
  return daysStr.split(',').map((d) => {
    const num = parseInt(d.trim());
    return t(DAY_KEYS[num - 1] || '');
  }).join(', ');
}

export function createEmptyTrigger(): ITrigger {
  return {
    id: 0,
    name: '',
    enabled: true,
    when_start_time: null,
    when_end_time: null,
    when_days: null,
    conditions: [{ condition_type: 'inverter', field: 'soc', op: '>=', value: 100 }],
    actions: [{ action_type: 'tuya_on', device_id: null, dps_key: '1', params: {} }],
    action_type: 'tuya_on',
    action_device_id: null,
    action_params: null,
    cooldown_seconds: 300,
    last_triggered_at: null,
    created_at: '',
  };
}

export function resolveActions(tr: ITrigger): ITriggerAction[] {
  if (tr.actions && tr.actions.length > 0) return tr.actions;
  return [{ action_type: tr.action_type, device_id: tr.action_device_id, dps_key: tr.action_params?.dp ? String(tr.action_params.dp) : '1', params: tr.action_params || {} }];
}
