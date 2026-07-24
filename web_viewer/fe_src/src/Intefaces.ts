export interface IInverterData {
  status: number;
  status_text: string;
  v_pv_1: number;
  v_pv_2: number;
  v_pv_3: number;
  v_bat: number;
  soc: number;
  soh: number;
  internal_fault: number;
  p_pv: number;
  p_pv_1: number;
  p_pv_2: number;
  p_pv_3: number;
  p_charge: number;
  p_discharge: number;
  vacr: number;
  vacs: number;
  vact: number;
  fac: number;
  p_inv: number;
  p_rec: number;
  _unknown_i1_51_52: number;
  pf: number;
  v_eps_r: number;
  v_eps_s: number;
  v_eps_t: number;
  f_eps: number;
  p_eps: number;
  s_eps: number;
  p_to_grid: number;
  p_to_user: number;
  e_pv_day: number;
  e_pv_1_day: number;
  e_pv_2_day: number;
  e_pv_3_day: number;
  e_inv_day: number;
  e_rec_day: number;
  e_chg_day: number;
  e_dischg_day: number;
  e_eps_day: number;
  e_to_grid_day: number;
  e_to_user_day: number;
  v_bus_1: number;
  v_bus_2: number;
  deviceTime: string;
  serial?: string;
  bat_capacity?: number;
}

export interface ICProps {
  inverterData: IInverterData;
  isSSEConnected: boolean;
}

export interface SeriesItem {
  x: number | string;
  y: never | number;
}

export interface IClassNameProps {
  className?: string;
}

export interface IUpdateChart {
  updateItem: (hourlyItem: never[]) => void;
}

export interface ITotal {
  pv: number;
  battery_charged: number;
  battery_discharged: number;
  grid_import: number;
  grid_export: number;
  consumption: number;
}

export interface IFetchChart {
  fetchChart: () => void;
}

export interface INotificationData {
  id: number;
  title: string;
  body: string;
  notified_at: string | number;
  read: number;
}

export interface ITuyaDevice {
  id: string;
  name: string;
  ip: string;
  local_key: string;
  protocol_version: string;
  device_type: string;
  created_at: string;
}

export interface ITriggerCondition {
  condition_type?: 'inverter' | 'device';
  field?: string;
  device_id?: string;
  dps_key?: string;
  dps_code?: string;
  op: string;
  value?: number;
  compare_value?: string | number | boolean;
}

export interface ITriggerAction {
  action_type: string;
  device_id: string | null;
  dps_key?: string;
  params?: {
    dp?: number;
    value?: boolean | number | string;
    notification_title?: string;
    notification_body?: string;
    audio_url?: string;
    audio_repeat?: number;
    audio_wait?: number;
  };
}

export interface ITriggerActionParams {
  dp?: number;
  value?: boolean | number | string;
  notification_title?: string;
  notification_body?: string;
  audio_url?: string;
  audio_repeat?: number;
  audio_wait?: number;
}

export interface ITrigger {
  id: number;
  name: string;
  enabled: boolean;
  when_start_time: string | null;
  when_end_time: string | null;
  when_days: string | null;
  conditions: ITriggerCondition[];
  actions?: ITriggerAction[];
  action_type: string;
  action_device_id: string | null;
  action_params: ITriggerActionParams | null;
  cooldown_seconds: number;
  last_triggered_at: string | null;
  created_at: string;
}

export interface IScannedDevice {
  ip: string;
  gwId: string;
  version: string;
  product_id: string;
  name: string;
  local_key: string;
}

export interface IDpsMapping {
  code: string;
  type: 'Boolean' | 'Integer' | 'Enum' | 'String' | 'Raw';
  values: {
    unit?: string;
    min?: number;
    max?: number;
    scale?: number;
    step?: number;
    range?: string[];
  };
}

export interface IDeviceMapping {
  name: string;
  mapping: Record<string, IDpsMapping>;
}

export interface ITriggerHistory {
  id: number;
  trigger_id: number;
  triggered_at: string;
  status: string;
  message: string;
  actions_detail?: string;
}
