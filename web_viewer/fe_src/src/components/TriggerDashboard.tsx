import { useState, useEffect, useCallback, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { ITuyaDevice, ITrigger, ITriggerCondition, ITriggerAction, IScannedDevice, IDeviceMapping, IDpsMapping, ITriggerHistory } from '../Intefaces';
import Loading from './Loading';
import * as logUtil from '../utils/logUtil';
import './TriggerDashboard.css';

interface TriggerDashboardProps {
  onClose: () => void;
}

const INVERTER_FIELDS = [
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

const CONDITION_OPS = ['>', '<', '>=', '<=', '==', '!='];

const DAY_KEYS = ['triggers.dayMon', 'triggers.dayTue', 'triggers.dayWed', 'triggers.dayThu', 'triggers.dayFri', 'triggers.daySat', 'triggers.daySun'];

function getDeviceDpsList(deviceId: string | null | undefined, mappings: Record<string, IDeviceMapping>): [string, IDpsMapping][] {
  if (!deviceId || !mappings[deviceId]) return [];
  return Object.entries(mappings[deviceId].mapping);
}

function getActionTypeOptions(): { value: string; label: string }[] {
  return [
    { value: 'notification', label: 'triggers.actionNotification' },
    { value: 'play_audio', label: 'triggers.actionPlayAudio' },
    { value: 'tuya_on', label: 'triggers.actionTurnOn' },
    { value: 'tuya_off', label: 'triggers.actionTurnOff' },
    { value: 'tuya_set', label: 'triggers.actionSetValue' },
  ];
}

function formatDays(daysStr: string | null, t: (key: string) => string): string {
  if (!daysStr) return '';
  return daysStr.split(',').map((d) => {
    const num = parseInt(d.trim());
    return t(DAY_KEYS[num - 1] || '');
  }).join(', ');
}

function TriggerDashboard({ onClose }: TriggerDashboardProps) {
  const { t } = useTranslation();
  const [activeTab, setActiveTab] = useState<'devices' | 'triggers'>('triggers');
  const [devices, setDevices] = useState<ITuyaDevice[]>([]);
  const [triggers, setTriggers] = useState<ITrigger[]>([]);
  const [scannedDevices, setScannedDevices] = useState<IScannedDevice[]>([]);
  const [loading, setLoading] = useState(true);
  const [scanning, setScanning] = useState(false);
  const [editingTrigger, setEditingTrigger] = useState<ITrigger | null>(null);
  const [showAddDevice, setShowAddDevice] = useState(false);
  const [message, setMessage] = useState<{ text: string; type: 'success' | 'error' } | null>(null);
  const [wizardRun, setWizardRun] = useState<boolean>(true);
  const [deviceStatuses, setDeviceStatuses] = useState<Record<string, boolean>>({});
  const [deviceMappings, setDeviceMappings] = useState<Record<string, IDeviceMapping>>({});
  const [historyTriggerId, setHistoryTriggerId] = useState<number | null>(null);
  const [triggerHistory, setTriggerHistory] = useState<ITriggerHistory[]>([]);

  const fetchWizardStatus = useCallback(async () => {
    try {
      const res = await fetch(`${import.meta.env.VITE_API_BASE_URL}/tuya-devices/wizard-status`);
      const data = await res.json();
      setWizardRun(Boolean(data.wizard_run));
    } catch (err) {
      logUtil.error('Failed to fetch wizard status', err);
    }
  }, []);

  const fetchDevices = useCallback(async () => {
    try {
      const res = await fetch(`${import.meta.env.VITE_API_BASE_URL}/tuya-devices`);
      const data = await res.json();
      setDevices(data.devices || []);
    } catch (err) {
      logUtil.error('Failed to fetch devices', err);
    }
  }, []);

  const fetchTriggers = useCallback(async () => {
    try {
      const res = await fetch(`${import.meta.env.VITE_API_BASE_URL}/triggers`);
      const data = await res.json();
      setTriggers(data.triggers || []);
    } catch (err) {
      logUtil.error('Failed to fetch triggers', err);
    }
  }, []);

  const fetchDeviceMappings = useCallback(async () => {
    try {
      const res = await fetch(`${import.meta.env.VITE_API_BASE_URL}/tuya-devices/mappings`);
      const data = await res.json();
      setDeviceMappings(data.mappings || {});
    } catch (err) {
      logUtil.error('Failed to fetch device mappings', err);
    }
  }, []);

  const fetchDeviceStatuses = useCallback(async (devs: ITuyaDevice[]) => {
    if (devs.length === 0) return;
    try {
      const res = await fetch(`${import.meta.env.VITE_API_BASE_URL}/tuya-devices/batch-status`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids: devs.map((d) => d.id) }),
      });
      const data = await res.json();
      if (data.success && data.statuses) {
        const statuses: Record<string, boolean> = {};
        for (const [id, st] of Object.entries(data.statuses)) {
          const s = st as { dps?: Record<string, unknown>; error?: string };
          if (s.dps) {
            statuses[id] = Boolean(s.dps['1']);
          } else if (s.error) {
            statuses[id] = false;
          }
        }
        setDeviceStatuses(statuses);
      }
    } catch (err) {
      logUtil.error('Batch status fetch failed', err);
    }
  }, []);

  useEffect(() => {
    const load = async () => {
      setLoading(true);
      await Promise.all([fetchDevices(), fetchTriggers(), fetchWizardStatus(), fetchDeviceMappings()]);
      setLoading(false);
    };
    load();
  }, [fetchDevices, fetchTriggers, fetchWizardStatus, fetchDeviceMappings]);

  useEffect(() => {
    if (devices.length > 0) {
      fetchDeviceStatuses(devices);
    }
  }, [devices, fetchDeviceStatuses]);

  useEffect(() => {
    if (activeTab === 'devices' && devices.length > 0) {
      fetchDeviceStatuses(devices);
    }
  }, [activeTab, devices, fetchDeviceStatuses]);

  const handleScan = async () => {
    setScanning(true);
    setScannedDevices([]);
    try {
      const res = await fetch(`${import.meta.env.VITE_API_BASE_URL}/tuya-devices/scan`, { method: 'POST' });
      const data = await res.json();
      setScannedDevices(data.devices || []);
      if (!data.devices || data.devices.length === 0) {
        setMessage({ text: t('triggers.noDevicesFound'), type: 'error' });
      }
    } catch (err) {
      logUtil.error('Scan failed', err);
      setMessage({ text: t('triggers.scanFailed'), type: 'error' });
    } finally {
      setScanning(false);
    }
  };

  const handleRegisterDevice = async (scanned: IScannedDevice) => {
    const name = prompt(t('triggers.enterDeviceName'), scanned.name || 'Tuya Device');
    if (!name) return;
    let localKey = scanned.local_key;
    if (!localKey) {
      const input = prompt(t('triggers.enterLocalKey'));
      if (!input) return;
      localKey = input;
    }
    try {
      const res = await fetch(`${import.meta.env.VITE_API_BASE_URL}/tuya-devices`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          id: scanned.gwId,
          name,
          ip: scanned.ip,
          local_key: localKey,
          protocol_version: scanned.version || '3.3',
          device_type: 'outlet',
        }),
      });
      const data = await res.json();
      if (data.success) {
        setMessage({ text: t('triggers.deviceRegistered'), type: 'success' });
        await fetchDevices();
        await fetchDeviceMappings();
      } else {
        setMessage({ text: data.message || t('triggers.registerFailed'), type: 'error' });
      }
    } catch (err) {
      logUtil.error('Register failed', err);
      setMessage({ text: t('triggers.registerFailed'), type: 'error' });
    }
  };

  const handleDeleteDevice = async (id: string) => {
    if (!confirm(t('triggers.confirmDeleteDevice'))) return;
    try {
      await fetch(`${import.meta.env.VITE_API_BASE_URL}/tuya-devices/${id}`, { method: 'DELETE' });
      await fetchDevices();
      setMessage({ text: t('triggers.deviceDeleted'), type: 'success' });
    } catch (err) {
      logUtil.error('Delete failed', err);
    }
  };

  const handleTestDevice = async (id: string, action: 'turn_on' | 'turn_off') => {
    try {
      const res = await fetch(`${import.meta.env.VITE_API_BASE_URL}/tuya-devices/${id}/control`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action }),
      });
      const data = await res.json();
      if (data.error) {
        setMessage({ text: data.error, type: 'error' });
      } else {
        setMessage({ text: t('triggers.commandSent'), type: 'success' });
        setDeviceStatuses((prev) => ({ ...prev, [id]: action === 'turn_on' }));
      }
    } catch (err) {
      logUtil.error('Control failed', err);
      setMessage({ text: t('triggers.controlFailed'), type: 'error' });
    }
  };

  const handleDeleteTrigger = async (id: number) => {
    if (!confirm(t('triggers.confirmDeleteTrigger'))) return;
    try {
      await fetch(`${import.meta.env.VITE_API_BASE_URL}/triggers/${id}`, { method: 'DELETE' });
      await fetchTriggers();
      setMessage({ text: t('triggers.triggerDeleted'), type: 'success' });
    } catch (err) {
      logUtil.error('Delete trigger failed', err);
    }
  };

  const handleTestTrigger = async (id: number) => {
    try {
      const res = await fetch(`${import.meta.env.VITE_API_BASE_URL}/triggers/${id}/test`, { method: 'POST' });
      const data = await res.json();
      if (data.success) {
        setMessage({ text: t('triggers.triggerTested'), type: 'success' });
      } else {
        setMessage({ text: data.message || t('triggers.testFailed'), type: 'error' });
      }
    } catch (err) {
      logUtil.error('Test trigger failed', err);
      setMessage({ text: t('triggers.testFailed'), type: 'error' });
    }
  };

  const handleToggleTrigger = async (trigger: ITrigger) => {
    try {
      await fetch(`${import.meta.env.VITE_API_BASE_URL}/triggers`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...trigger, enabled: !trigger.enabled }),
      });
      await fetchTriggers();
    } catch (err) {
      logUtil.error('Toggle trigger failed', err);
    }
  };

  const handleShowHistory = async (triggerId: number) => {
    setHistoryTriggerId(triggerId);
    try {
      const res = await fetch(`${import.meta.env.VITE_API_BASE_URL}/triggers/${triggerId}/history`);
      const data = await res.json();
      setTriggerHistory(data.history || []);
    } catch (err) {
      logUtil.error('Failed to fetch trigger history', err);
      setTriggerHistory([]);
    }
  };

  if (loading) {
    return (
      <div className="trigger-dashboard-overlay">
        <div className="trigger-dashboard">
          <div className="trigger-dashboard-header">
            <h3>{t('triggers.title')}</h3>
            <button className="close-popover" onClick={onClose}>×</button>
          </div>
          <Loading />
        </div>
      </div>
    );
  }

  if (editingTrigger) {
    return (
      <TriggerForm
        trigger={editingTrigger}
        devices={devices}
        deviceMappings={deviceMappings}
        onSave={async (data) => {
          try {
            const res = await fetch(`${import.meta.env.VITE_API_BASE_URL}/triggers`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(data),
            });
            const result = await res.json();
            if (result.success) {
              setMessage({ text: t('triggers.triggerSaved'), type: 'success' });
              setEditingTrigger(null);
              await fetchTriggers();
            } else {
              setMessage({ text: result.message || t('triggers.saveFailed'), type: 'error' });
            }
          } catch (err) {
            logUtil.error('Save trigger failed', err);
            setMessage({ text: t('triggers.saveFailed'), type: 'error' });
          }
        }}
        onCancel={() => setEditingTrigger(null)}
      />
    );
  }

  return (
    <div className="trigger-dashboard-overlay">
      <div className="trigger-dashboard">
        <div className="trigger-dashboard-header">
          <h3>{t('triggers.title')}</h3>
          {message && (
            <div className={`trigger-message ${message.type}`}>{message.text}</div>
          )}
          <button className="close-popover" onClick={onClose}>×</button>
        </div>
        <div className="trigger-tabs">
          <button
            className={activeTab === 'triggers' ? 'active' : ''}
            onClick={() => setActiveTab('triggers')}
          >
            {t('triggers.tabTriggers')}
          </button>
          <button
            className={activeTab === 'devices' ? 'active' : ''}
            onClick={() => setActiveTab('devices')}
          >
            {t('triggers.tabDevices')}
          </button>
        </div>
        <div className="trigger-content">
          {activeTab === 'devices' && (
            <DevicesTab
              devices={devices}
              scannedDevices={scannedDevices}
              scanning={scanning}
              wizardRun={wizardRun}
              deviceStatuses={deviceStatuses}
              onScan={handleScan}
              onRegister={handleRegisterDevice}
              onDelete={handleDeleteDevice}
              onTest={handleTestDevice}
              onAddDevice={() => setShowAddDevice(!showAddDevice)}
            />
          )}
          {activeTab === 'triggers' && (
            <TriggersTab
              triggers={triggers}
              devices={devices}
              deviceMappings={deviceMappings}
              onAdd={() => setEditingTrigger(createEmptyTrigger())}
              onEdit={(t) => setEditingTrigger(t)}
              onDelete={handleDeleteTrigger}
              onTest={handleTestTrigger}
              onToggle={handleToggleTrigger}
              onShowHistory={handleShowHistory}
              historyTriggerId={historyTriggerId}
              triggerHistory={triggerHistory}
              onCloseHistory={() => { setHistoryTriggerId(null); setTriggerHistory([]); }}
            />
          )}
        </div>
      </div>
    </div>
  );
}

function createEmptyTrigger(): ITrigger {
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

interface DevicesTabProps {
  devices: ITuyaDevice[];
  scannedDevices: IScannedDevice[];
  scanning: boolean;
  wizardRun: boolean;
  deviceStatuses: Record<string, boolean>;
  onScan: () => void;
  onRegister: (d: IScannedDevice) => void;
  onDelete: (id: string) => void;
  onTest: (id: string, action: 'turn_on' | 'turn_off') => void;
  onAddDevice: () => void;
}

function DevicesTab({ devices, scannedDevices, scanning, wizardRun, deviceStatuses, onScan, onRegister, onDelete, onTest }: DevicesTabProps) {
  const { t } = useTranslation();
  const registeredIds = new Set(devices.map((d) => d.id));
  const filteredScanned = scannedDevices.filter((s) => !registeredIds.has(s.gwId));

  return (
    <div className="devices-tab">
      {!wizardRun && (
        <div className="wizard-warning">
          {t('triggers.wizardWarning')}
        </div>
      )}
      <div className="devices-actions">
        <button onClick={onScan} disabled={scanning} className="scan-btn">
          {scanning ? t('triggers.scanning') : t('triggers.scanForDevices')}
        </button>
      </div>
      {filteredScanned.length > 0 && (
        <div className="devices-section">
          <h4>{t('triggers.discoveredDevices')}</h4>
          {filteredScanned.map((d, i) => (
            <div key={i} className="device-card discovered">
              <div className="device-info">
                <span className="device-name">{d.name || t('triggers.unknownDevice')}</span>
                <span className="device-detail">ID: {d.gwId}</span>
                <span className="device-detail">IP: {d.ip}</span>
                <span className="device-detail">v{d.version}</span>
                {d.local_key && (
                  <span className="device-detail device-key">
                    {t('triggers.key')}: {d.local_key.slice(0, 4)}...{d.local_key.slice(-4)}
                  </span>
                )}
              </div>
              <button onClick={() => onRegister(d)} className="register-btn">
                {t('triggers.register')}
              </button>
            </div>
          ))}
        </div>
      )}
      <div className="devices-section">
        <h4>{t('triggers.registeredDevices')}</h4>
        {devices.length === 0 ? (
          <div className="no-records">{t('triggers.noDevices')}</div>
        ) : (
          <>
            <div className="tuya-only-notice">{t('triggers.tuyaOnlyNotice')}</div>
            {devices.map((d) => {
            const isOn = deviceStatuses[d.id] || false;
            return (
              <div key={d.id} className="device-card registered">
                <div className="device-info">
                  <span className="device-name">{d.name}</span>
                  <span className="device-detail">ID: {d.id}</span>
                  <span className="device-detail">IP: {d.ip}</span>
                </div>
                <div className="device-actions">
                  <button
                    onClick={() => onTest(d.id, isOn ? 'turn_off' : 'turn_on')}
                    className={`test-btn ${isOn ? 'off' : 'on'}`}
                  >
                    {isOn ? t('triggers.testOff') : t('triggers.testOn')}
                  </button>
                  <button onClick={() => onDelete(d.id)} className="delete-btn">
                    {t('triggers.delete')}
                  </button>
                </div>
              </div>
            );
          })}
          </>
        )}
      </div>
    </div>
  );
}

interface TriggersTabProps {
  triggers: ITrigger[];
  devices: ITuyaDevice[];
  deviceMappings: Record<string, IDeviceMapping>;
  onAdd: () => void;
  onEdit: (t: ITrigger) => void;
  onDelete: (id: number) => void;
  onTest: (id: number) => void;
  onToggle: (t: ITrigger) => void;
  onShowHistory: (id: number) => void;
  historyTriggerId: number | null;
  triggerHistory: ITriggerHistory[];
  onCloseHistory: () => void;
}

function TriggersTab({ triggers, devices, deviceMappings, onAdd, onEdit, onDelete, onTest, onToggle, onShowHistory, historyTriggerId, triggerHistory, onCloseHistory }: TriggersTabProps) {
  const { t } = useTranslation();

  const resolveActions = (tr: ITrigger): ITriggerAction[] => {
    if (tr.actions && tr.actions.length > 0) return tr.actions;
    return [{ action_type: tr.action_type, device_id: tr.action_device_id, dps_key: tr.action_params?.dp ? String(tr.action_params.dp) : '1', params: tr.action_params || {} }];
  };

  const resolveConditionLabel = (c: ITriggerCondition): string => {
    if (c.condition_type === 'device') {
      const dev = devices.find((d) => d.id === c.device_id);
      const devName = dev?.name || c.device_id;
      const mapping = deviceMappings[c.device_id || ''];
      const dps = mapping?.mapping?.[c.dps_key || ''];
      let dpsLabel = dps?.code || c.dps_key || '1';
      let displayValue = String(c.compare_value ?? '');
      if (dps?.type === 'Boolean') {
        displayValue = c.compare_value === true || c.compare_value === 'true' ? t('triggers.testOn') : t('triggers.testOff');
      } else if (dps?.type === 'Enum') {
        displayValue = String(c.compare_value ?? '');
      }
      return `${devName} [${dpsLabel}] ${c.op} ${displayValue}`;
    }
    const found = INVERTER_FIELDS.find((f) => f.value === c.field);
    const fieldLabel = found ? t(found.label) : (c.field || '');
    return `${fieldLabel} ${c.op} ${c.value}`;
  };

  const resolveActionLabel = (a: ITriggerAction): { typeLabel: string; targetLabel: string } => {
    if (a.action_type === 'notification') {
      return { typeLabel: t('triggers.actionNotification'), targetLabel: a.params?.notification_title || '' };
    }
    if (a.action_type === 'play_audio') {
      return { typeLabel: t('triggers.actionPlayAudio'), targetLabel: a.params?.audio_url || '' };
    }
    const actionMap: Record<string, string> = {
      tuya_on: t('triggers.actionTurnOn'),
      tuya_off: t('triggers.actionTurnOff'),
      tuya_toggle: t('triggers.actionToggle'),
      tuya_set: t('triggers.actionSetValue'),
    };
    const typeLabel = actionMap[a.action_type] || a.action_type;
    const dev = devices.find((d) => d.id === a.device_id);
    const devName = dev?.name || a.device_id || '';
    let dpsLabel = '';
    if (a.action_type === 'tuya_set' && a.dps_key && a.device_id) {
      const mapping = deviceMappings[a.device_id];
      if (mapping?.mapping?.[a.dps_key]) {
        dpsLabel = ` [${mapping.mapping[a.dps_key].code}]`;
      } else {
        dpsLabel = ` [${a.dps_key}]`;
      }
    }
    return { typeLabel, targetLabel: `${devName}${dpsLabel}` };
  };

  const translateHistoryMessage = (msg: string): string => {
    if (!msg) return '';
    const actionMap: Record<string, string> = {
      tuya_on: t('triggers.actionTurnOn'),
      tuya_off: t('triggers.actionTurnOff'),
      tuya_toggle: t('triggers.actionToggle'),
      tuya_set: t('triggers.actionSetValue'),
      notification: t('triggers.actionNotification'),
      play_audio: t('triggers.actionPlayAudio'),
    };
    return msg.split(',').map((part) => {
      const trimmed = part.trim();
      const manualPrefix = 'Manual test: ';
      if (trimmed.startsWith(manualPrefix)) {
        const actionPart = trimmed.slice(manualPrefix.length);
        return `${t('triggers.testNow')}: ${actionMap[actionPart.trim()] || actionPart.trim()}`;
      }
      return actionMap[trimmed] || trimmed;
    }).join(', ');
  };

  const renderActionsDetail = (actionsDetail: string, message: string): string => {
    if (!actionsDetail) return translateHistoryMessage(message);
    try {
      const actions: ITriggerAction[] = JSON.parse(actionsDetail);
      if (!Array.isArray(actions) || actions.length === 0) return translateHistoryMessage(message);
      return actions.map((a: ITriggerAction) => {
        if (a.action_type === 'notification') {
          return `${t('triggers.actionNotification')}: ${a.params?.notification_title || ''}`;
        }
        const dev = devices.find((d) => d.id === a.device_id);
        const devName = dev?.name || a.device_id;
        const typeMap: Record<string, string> = {
          tuya_on: t('triggers.actionTurnOn'),
          tuya_off: t('triggers.actionTurnOff'),
          tuya_toggle: t('triggers.actionToggle'),
          tuya_set: t('triggers.actionSetValue'),
        };
        const typeLabel = typeMap[a.action_type] || a.action_type;
        if (a.action_type === 'tuya_set') {
          const val = a.params?.value ?? '';
          const mapping = deviceMappings[a.device_id || ''];
          const dps = mapping?.mapping?.[a.dps_key || ''];
          const dpsLabel = dps?.code || a.dps_key || '';
          return `${typeLabel} ${devName}${dpsLabel ? ` [${dpsLabel}]` : ''} = ${val}`;
        }
        const mapping = deviceMappings[a.device_id || ''];
        const dps = mapping?.mapping?.[a.dps_key || ''];
        const dpsLabel = dps?.code || a.dps_key || '';
        return `${typeLabel} ${devName}${dpsLabel ? ` [${dpsLabel}]` : ''}`;
      }).join(', ');
    } catch {
      return translateHistoryMessage(message);
    }
  };

  return (
    <div className="triggers-tab">
      <div className="triggers-actions">
        <button onClick={onAdd} className="add-trigger-btn">
          {t('triggers.addTrigger')}
        </button>
      </div>
      {triggers.length === 0 ? (
        <div className="no-records">{t('triggers.noTriggers')}</div>
      ) : (
        triggers.map((tr) => {
          const actions = resolveActions(tr);
          return (
            <div key={tr.id} className={`trigger-card ${tr.enabled ? 'enabled' : 'disabled'}`}>
              <div className="trigger-header">
                <span className="trigger-name">{tr.name}</span>
                <label className="trigger-toggle">
                  <input
                    type="checkbox"
                    checked={tr.enabled}
                    onChange={() => onToggle(tr)}
                  />
                  <span className="trigger-toggle-slider"></span>
                </label>
              </div>
              <div className="trigger-details">
                {tr.when_start_time && tr.when_end_time && (
                  <div className="trigger-detail">
                    <strong>{t('triggers.when')}:</strong>                     {tr.when_start_time} - {tr.when_end_time}
                    {tr.when_days && ` (${formatDays(tr.when_days, t)})`}
                  </div>
                )}
                <div className="trigger-detail">
                  <strong>{t('triggers.if')}:</strong>{' '}
                  {tr.conditions.map((c, i) => (
                    <span key={i}>
                      {i > 0 && ` ${t('triggers.and')} `}
                      {resolveConditionLabel(c)}
                    </span>
                  ))}
                </div>
                <div className="trigger-detail">
                  <strong>{t('triggers.then')}:</strong>{' '}
                  {actions.map((a, i) => {
                    const { typeLabel, targetLabel } = resolveActionLabel(a);
                    return (
                      <span key={i}>
                        {i > 0 && ' + '}
                        {typeLabel}
                        {targetLabel && <> → {targetLabel}</>}
                      </span>
                    );
                  })}
                </div>
                <div className="trigger-detail">
                  <strong>{t('triggers.cooldown')}:</strong> {tr.cooldown_seconds}s
                </div>
                {tr.last_triggered_at && (
                  <div className="trigger-detail">
                    <strong>{t('triggers.lastTriggered')}:</strong> {new Date(tr.last_triggered_at).toLocaleString()}
                  </div>
                )}
              </div>
              <div className="trigger-actions">
                <button onClick={() => onEdit(tr)} className="edit-btn">{t('triggers.edit')}</button>
                <button onClick={() => onTest(tr.id)} className="test-btn">{t('triggers.testNow')}</button>
                <button onClick={() => onShowHistory(tr.id)} className="history-btn">{t('triggers.history')}</button>
                <button onClick={() => onDelete(tr.id)} className="delete-btn">{t('triggers.delete')}</button>
              </div>
              {historyTriggerId === tr.id && (
                <div className="trigger-history-panel">
                  <div className="trigger-history-header">
                    <span>{t('triggers.history')}</span>
                    <button className="close-popover" onClick={onCloseHistory}>×</button>
                  </div>
                  {triggerHistory.length === 0 ? (
                    <div className="no-records">{t('triggers.noHistory')}</div>
                  ) : (
                    <div className="trigger-history-list">
                      {triggerHistory.map((h) => (
                        <div key={h.id} className={`trigger-history-item ${h.status}`}>
                          <span className="history-time">{new Date(h.triggered_at).toLocaleString()}</span>
                          <span className={`history-status ${h.status}`}>{h.status === 'success' ? '✓' : '✗'}</span>
                          <span className="history-message">{renderActionsDetail(h.actions_detail || '', h.message)}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        })
      )}
    </div>
  );
}

interface TriggerFormProps {
  trigger: ITrigger;
  devices: ITuyaDevice[];
  deviceMappings: Record<string, IDeviceMapping>;
  onSave: (data: ITrigger) => void;
  onCancel: () => void;
}

function TriggerForm({ trigger, devices, deviceMappings, onSave, onCancel }: TriggerFormProps) {
  const { t } = useTranslation();
  const [data, setData] = useState<ITrigger>({ ...trigger });
  const [conditions, setConditions] = useState<ITriggerCondition[]>(
    trigger.conditions.length > 0
      ? trigger.conditions.map((c) => ({ condition_type: 'inverter', ...c }))
      : [{ condition_type: 'inverter', field: 'soc', op: '>=', value: 100 }]
  );
  const [actions, setActions] = useState<ITriggerAction[]>(() => {
    if (trigger.actions && trigger.actions.length > 0) return [...trigger.actions];
    return [{ action_type: trigger.action_type || 'tuya_on', device_id: trigger.action_device_id, dps_key: trigger.action_params?.dp ? String(trigger.action_params.dp) : '1', params: trigger.action_params || {} }];
  });
  const [selectedDays, setSelectedDays] = useState<number[]>(() => {
    if (!trigger.when_days) return [];
    return trigger.when_days.split(',').map(Number).filter(Boolean);
  });
  const notifTitleRef = useRef<HTMLInputElement>(null);
  const notifBodyRef = useRef<HTMLTextAreaElement>(null);
  const [showDescParam, setShowDescParam] = useState<{ actionIdx: number; param: string } | null>(null);
  const clickTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const insertParam = (param: string, actionIdx: number) => {
    const activeEl = document.activeElement;
    const isTitle = activeEl === notifTitleRef.current;
    const isBody = activeEl === notifBodyRef.current;
    if (!isTitle && !isBody) return;
    const el = activeEl as HTMLInputElement | HTMLTextAreaElement;
    const start = el.selectionStart ?? el.value.length;
    const end = el.selectionEnd ?? el.value.length;
    const newVal = el.value.slice(0, start) + param + el.value.slice(end);
    if (isTitle) {
      updateActionParams(actionIdx, { notification_title: newVal });
    } else {
      updateActionParams(actionIdx, { notification_body: newVal });
    }
    requestAnimationFrame(() => {
      el.focus();
      el.setSelectionRange(start + param.length, start + param.length);
    });
  };

  const handleParamClick = (param: string, actionIdx: number) => {
    if (clickTimerRef.current) clearTimeout(clickTimerRef.current);
    clickTimerRef.current = setTimeout(() => {
      insertParam(param, actionIdx);
    }, 200);
  };

  const handleParamDoubleClick = (e: React.MouseEvent, param: string, actionIdx: number) => {
    e.preventDefault();
    if (clickTimerRef.current) clearTimeout(clickTimerRef.current);
    const current = showDescParam?.actionIdx === actionIdx && showDescParam?.param === param;
    setShowDescParam(current ? null : { actionIdx, param });
  };

  const toggleDay = (day: number) => {
    setSelectedDays((prev) =>
      prev.includes(day) ? prev.filter((d) => d !== day) : [...prev, day]
    );
  };

  const handleSave = () => {
    const payload: ITrigger = {
      ...data,
      conditions,
      actions,
      when_days: selectedDays.length > 0 ? selectedDays.join(',') : null,
      when_start_time: data.when_start_time || null,
      when_end_time: data.when_end_time || null,
    };
    onSave(payload);
  };

  const updateCondition = (index: number, patch: Partial<ITriggerCondition>) => {
    setConditions((prev) => prev.map((c, i) => (i === index ? { ...c, ...patch } : c)));
  };

  const updateAction = (index: number, patch: Partial<ITriggerAction>) => {
    setActions((prev) => prev.map((a, i) => (i === index ? { ...a, ...patch } : a)));
  };

  const updateActionParams = (index: number, patch: Record<string, unknown>) => {
    setActions((prev) => prev.map((a, i) => (i === index ? { ...a, params: { ...a.params, ...patch } } : a)));
  };

  const getNotificationParams = (): { param: string; label: string }[] => {
    const params: { param: string; label: string }[] = [];
    const hasInverter = conditions.some((c) => c.condition_type === 'inverter');
    const deviceIds = [...new Set(conditions.filter((c) => c.condition_type === 'device' && c.device_id).map((c) => c.device_id!))];

    if (hasInverter) {
      for (const f of INVERTER_FIELDS) {
        params.push({ param: `$${f.value}`, label: t(f.label) });
      }
    }

    for (const deviceId of deviceIds) {
      const dev = devices.find((d) => d.id === deviceId);
      const devName = (dev?.name || deviceId).replace(/ /g, '_');
      const mapping = deviceMappings[deviceId];
      if (mapping?.mapping) {
        for (const [key, dps] of Object.entries(mapping.mapping)) {
          const dpsCode = dps.code || key;
          params.push({ param: `$${devName}_${dpsCode}`, label: `${dev?.name || deviceId} [${dpsCode}]` });
        }
      } else {
        params.push({ param: `$${devName}_1`, label: `${dev?.name || deviceId} [1]` });
      }
    }

    return params;
  };

  return (
    <div className="trigger-dashboard-overlay">
      <div className="trigger-dashboard trigger-form-container">
        <div className="trigger-dashboard-header">
          <h3>{data.id ? t('triggers.editTrigger') : t('triggers.addTrigger')}</h3>
          <button className="close-popover" onClick={onCancel}>×</button>
        </div>
        <div className="trigger-form">
          <div className="form-group">
            <label>{t('triggers.name')}</label>
            <input
              type="text"
              value={data.name}
              onChange={(e) => setData({ ...data, name: e.target.value })}
              placeholder={t('triggers.namePlaceholder')}
            />
          </div>

          <div className="form-section">
            <h4>{t('triggers.whenSection')}</h4>
            <div className="form-row">
              <div className="form-group">
                <label>{t('triggers.startTime')}</label>
                <input
                  type="time"
                  value={data.when_start_time || ''}
                  onChange={(e) => setData({ ...data, when_start_time: e.target.value || null })}
                />
              </div>
              <div className="form-group">
                <label>{t('triggers.endTime')}</label>
                <input
                  type="time"
                  value={data.when_end_time || ''}
                  onChange={(e) => setData({ ...data, when_end_time: e.target.value || null })}
                />
              </div>
            </div>
            <div className="form-group">
              <label>{t('triggers.days')}</label>
              <div className="day-picker">
                {DAY_KEYS.map((key, i) => (
                  <button
                    key={i}
                    type="button"
                    className={`day-btn ${selectedDays.includes(i + 1) ? 'selected' : ''}`}
                    onClick={() => toggleDay(i + 1)}
                  >
                    {t(key)}
                  </button>
                ))}
              </div>
            </div>
          </div>

          <div className="form-section">
            <h4>{t('triggers.conditionsSection')}</h4>
            {conditions.map((cond, i) => (
              <div key={i} className="condition-row">
                <select
                  value={cond.condition_type || 'inverter'}
                  onChange={(e) => {
                    const ct = e.target.value as 'inverter' | 'device';
                    updateCondition(i, {
                      condition_type: ct,
                      field: ct === 'inverter' ? (cond.field || 'soc') : undefined,
                      device_id: ct === 'device' ? (cond.device_id || '') : undefined,
                      dps_key: ct === 'device' ? (cond.dps_key || '1') : undefined,
                      dps_code: ct === 'device' ? (cond.dps_code || '') : undefined,
                      op: ct === 'device' ? '==' : (cond.op || '>='),
                      compare_value: ct === 'device' ? (cond.compare_value ?? '') : undefined,
                      value: ct === 'inverter' ? (cond.value ?? 0) : undefined,
                    });
                  }}
                  className="condition-type-select"
                >
                  <option value="inverter">{t('triggers.condTypeInverter')}</option>
                  <option value="device">{t('triggers.condTypeDevice')}</option>
                </select>

                {cond.condition_type === 'device' ? (
                  <>
                    <select
                      value={cond.device_id || ''}
                      onChange={(e) => {
                        const newDeviceId = e.target.value;
                        const dpsList = getDeviceDpsList(newDeviceId, deviceMappings);
                        updateCondition(i, {
                          device_id: newDeviceId,
                          dps_key: dpsList.length > 0 ? dpsList[0][0] : '1',
                          dps_code: dpsList.length > 0 ? dpsList[0][1].code : '',
                          compare_value: '',
                        });
                      }}
                      className="condition-device-select"
                    >
                      <option value="">{t('triggers.selectDevice')}</option>
                      {devices.map((d) => (
                        <option key={d.id} value={d.id}>{d.name}</option>
                      ))}
                    </select>
                    {cond.device_id && (() => {
                      const dpsList = getDeviceDpsList(cond.device_id, deviceMappings);
                      if (dpsList.length > 0) {
                        return (
                          <select
                            value={cond.dps_key || dpsList[0][0]}
                            onChange={(e) => {
                              const dpsKey = e.target.value;
                              const mapping = deviceMappings[cond.device_id || '']?.mapping?.[dpsKey];
                              updateCondition(i, {
                                dps_key: dpsKey,
                                dps_code: mapping?.code || '',
                                compare_value: '',
                              });
                            }}
                            className="condition-dps-select"
                          >
                            {dpsList.map(([key, dps]) => (
                              <option key={key} value={key}>
                                {dps.code || key} ({dps.type})
                              </option>
                            ))}
                          </select>
                        );
                      }
                      return (
                        <select
                          value={cond.dps_key || '1'}
                          onChange={(e) => updateCondition(i, { dps_key: e.target.value, compare_value: '' })}
                          className="condition-dps-select"
                        >
                          <option value="1">switch_1</option>
                          <option value="2">switch_2</option>
                          <option value="3">switch_3</option>
                        </select>
                      );
                    })()}
                    {(() => {
                      const dps = cond.device_id
                        ? deviceMappings[cond.device_id]?.mapping?.[cond.dps_key || '1']
                        : undefined;
                      const dpsType = dps?.type;
                      const comparisonOps = dpsType === 'Integer' ? CONDITION_OPS : ['==', '!='];
                      return (
                        <select
                          value={cond.op || '=='}
                          onChange={(e) => updateCondition(i, { op: e.target.value })}
                        >
                          {comparisonOps.map((op) => (
                            <option key={op} value={op}>{op}</option>
                          ))}
                        </select>
                      );
                    })()}
                    {(() => {
                      const dps = cond.device_id
                        ? deviceMappings[cond.device_id]?.mapping?.[cond.dps_key || '1']
                        : undefined;
                      const dpsType = dps?.type;
                      const dpsValues = dps?.values;

                      if (dpsType === 'Boolean') {
                        return (
                          <select
                            value={String(cond.compare_value ?? '')}
                            onChange={(e) => updateCondition(i, { compare_value: e.target.value === 'true' })}
                            className="condition-value"
                          >
                            <option value="">--</option>
                            <option value="true">{t('triggers.testOn')}</option>
                            <option value="false">{t('triggers.testOff')}</option>
                          </select>
                        );
                      }
                      if (dpsType === 'Enum' && dpsValues?.range) {
                        return (
                          <select
                            value={String(cond.compare_value ?? '')}
                            onChange={(e) => updateCondition(i, { compare_value: e.target.value })}
                            className="condition-value"
                          >
                            <option value="">--</option>
                            {dpsValues.range.map((v) => (
                              <option key={v} value={v}>{v}</option>
                            ))}
                          </select>
                        );
                      }
                      if (dpsType === 'Integer') {
                        return (
                          <input
                            type="number"
                            value={String(cond.compare_value ?? '')}
                            onChange={(e) => updateCondition(i, { compare_value: e.target.value })}
                            className="condition-value"
                            placeholder={dpsValues ? `${dpsValues.min ?? ''}~${dpsValues.max ?? ''}` : ''}
                          />
                        );
                      }
                      return (
                        <input
                          type="text"
                          value={String(cond.compare_value ?? '')}
                          onChange={(e) => updateCondition(i, { compare_value: e.target.value })}
                          className="condition-value"
                          placeholder={t('triggers.condValue')}
                        />
                      );
                    })()}
                  </>
                ) : (
                  <>
                    <select
                      value={cond.field || 'soc'}
                      onChange={(e) => updateCondition(i, { field: e.target.value })}
                    >
                      {INVERTER_FIELDS.map((f) => (
                        <option key={f.value} value={f.value}>{t(f.label)}</option>
                      ))}
                    </select>
                    <select
                      value={cond.op}
                      onChange={(e) => updateCondition(i, { op: e.target.value })}
                    >
                      {CONDITION_OPS.map((op) => (
                        <option key={op} value={op}>{op}</option>
                      ))}
                    </select>
                    <input
                      type="number"
                      value={cond.value ?? 0}
                      onChange={(e) => updateCondition(i, { value: parseFloat(e.target.value) || 0 })}
                      className="condition-value"
                    />
                  </>
                )}
                {conditions.length > 1 && (
                  <button
                    type="button"
                    className="remove-btn"
                    onClick={() => setConditions(conditions.filter((_, j) => j !== i))}
                  >
                    ×
                  </button>
                )}
              </div>
            ))}
            <button
              type="button"
              className="add-condition-btn"
              onClick={() => setConditions([...conditions, { condition_type: 'inverter', field: 'soc', op: '>=', value: 100 }])}
            >
              {t('triggers.addCondition')}
            </button>
          </div>

          <div className="form-section">
            <h4>{t('triggers.actionSection')}</h4>
            {actions.map((action, i) => {
              const dpsList = getDeviceDpsList(action.device_id, deviceMappings);
              const actionOptions = getActionTypeOptions();

              return (
                <div key={i} className="action-row">
                  <div className="action-row-header">
                    <span className="action-row-number">#{i + 1}</span>
                    {actions.length > 1 && (
                      <button
                        type="button"
                        className="remove-btn"
                        onClick={() => setActions(actions.filter((_, j) => j !== i))}
                      >
                        ×
                      </button>
                    )}
                  </div>
                  <div className="form-group">
                    <label>{t('triggers.actionType')}</label>
                    <select
                      value={action.action_type}
                      onChange={(e) => {
                        const newType = e.target.value;
                        updateAction(i, { action_type: newType });
                      }}
                    >
                      {action.action_type === 'notification' && !actionOptions.find(o => o.value === 'notification') && (
                        <option value="notification">{t('triggers.actionNotification')}</option>
                      )}
                      {actionOptions.map((opt) => (
                        <option key={opt.value} value={opt.value}>{t(opt.label)}</option>
                      ))}
                    </select>
                  </div>
                  {action.action_type === 'notification' ? (
                    <>
                      <div className="form-group">
                        <label>{t('triggers.notifTitle')}</label>
                        <input
                          ref={notifTitleRef}
                          type="text"
                          value={action.params?.notification_title || ''}
                          onChange={(e) => updateActionParams(i, { notification_title: e.target.value })}
                          placeholder={t('triggers.notifTitlePlaceholder')}
                        />
                      </div>
                      <div className="form-group">
                        <label>{t('triggers.notifBody')}</label>
                        <textarea
                          ref={notifBodyRef}
                          value={action.params?.notification_body || ''}
                          onChange={(e) => updateActionParams(i, { notification_body: e.target.value })}
                          placeholder={t('triggers.notifBodyPlaceholder')}
                          rows={2}
                        />
                      </div>
                      {(() => {
                        const params = getNotificationParams();
                        if (params.length === 0) return null;
                        return (
                          <div className="notification-params-hint">
                            <span className="params-label">{t('triggers.paramsHint')}:</span>
                            {params.map((p) => (
                              <button
                                key={p.param}
                                type="button"
                                className={`param-tag${showDescParam?.actionIdx === i && showDescParam?.param === p.param ? ' show-desc' : ''}`}
                                title={p.label}
                                onMouseDown={(e) => e.preventDefault()}
                                onClick={() => handleParamClick(p.param, i)}
                                onDoubleClick={(e) => { handleParamDoubleClick(e, p.param, i); }}
                              >
                                {p.param}<span className="param-desc">{p.label}</span>
                              </button>
                            ))}
                          </div>
                        );
                      })()}
                    </>
                  ) : action.action_type === 'play_audio' ? (
                    <>
                      <div className="form-group">
                        <label>{t('triggers.audioUrl')}</label>
                        <input
                          type="text"
                          value={action.params?.audio_url || ''}
                          onChange={(e) => updateActionParams(i, { audio_url: e.target.value })}
                          placeholder={t('triggers.audioUrlPlaceholder')}
                        />
                      </div>
                      <div className="form-group">
                        <label>{t('triggers.audioRepeat')}</label>
                        <input
                          type="number"
                          min={1}
                          value={action.params?.audio_repeat ?? 1}
                          onChange={(e) => updateActionParams(i, { audio_repeat: parseInt(e.target.value) || 1 })}
                        />
                      </div>
                      {(action.params?.audio_repeat ?? 1) > 1 && (
                        <div className="form-group">
                          <label>{t('triggers.audioWait')}</label>
                          <input
                            type="number"
                            min={1}
                            value={action.params?.audio_wait ?? 5}
                            onChange={(e) => updateActionParams(i, { audio_wait: parseInt(e.target.value) || 5 })}
                          />
                          <span className="field-hint">{t('triggers.audioWaitHint')}</span>
                        </div>
                      )}
                    </>
                  ) : (
                    <>
                      <div className="form-group">
                        <label>{t('triggers.device')}</label>
                        <select
                          value={action.device_id || ''}
                          onChange={(e) => {
                            const newDeviceId = e.target.value || null;
                            const newDpsList = getDeviceDpsList(newDeviceId, deviceMappings);
                            const firstDps = newDpsList.length > 0 ? newDpsList[0] : null;
                            updateAction(i, {
                              device_id: newDeviceId,
                              dps_key: firstDps ? firstDps[0] : '1',
                            });
                          }}
                        >
                          <option value="">{t('triggers.selectDevice')}</option>
                          {devices.map((d) => (
                            <option key={d.id} value={d.id}>{d.name}</option>
                          ))}
                        </select>
                      </div>
                      {action.device_id && action.action_type === 'tuya_set' && dpsList.length > 0 && (
                        <div className="form-group">
                          <label>{t('triggers.field')}</label>
                          <select
                            value={action.dps_key || (dpsList.length > 0 ? dpsList[0][0] : '1')}
                            onChange={(e) => updateAction(i, { dps_key: e.target.value })}
                          >
                            {dpsList.map(([key, dps]) => (
                              <option key={key} value={key}>
                                {dps.code || key} ({dps.type})
                              </option>
                            ))}
                          </select>
                        </div>
                      )}
                      {action.device_id && action.action_type === 'tuya_set' && dpsList.length === 0 && (
                        <div className="form-group">
                          <label>{t('triggers.field')}</label>
                          <select
                            value={action.dps_key || '1'}
                            onChange={(e) => updateAction(i, { dps_key: e.target.value })}
                          >
                            <option value="1">switch_1</option>
                            <option value="2">switch_2</option>
                            <option value="3">switch_3</option>
                          </select>
                        </div>
                      )}
                      {action.action_type === 'tuya_set' && (() => {
                        const dps = action.device_id
                          ? deviceMappings[action.device_id]?.mapping?.[action.dps_key || (dpsList.length > 0 ? dpsList[0][0] : '1')]
                          : undefined;
                        const dpsType = dps?.type;
                        const dpsValues = dps?.values;

                        if (dpsType === 'Integer') {
                          return (
                            <div className="form-group">
                              <label>{t('triggers.setValue')} ({dps!.code} [{dpsValues?.min ?? 0}~{dpsValues?.max ?? ''}])</label>
                              <input
                                type="number"
                                value={String(action.params?.value ?? '')}
                                onChange={(e) => updateActionParams(i, { value: parseFloat(e.target.value) || 0 })}
                                placeholder={dpsValues ? `${dpsValues.min ?? 0}~${dpsValues.max ?? ''}` : ''}
                              />
                            </div>
                          );
                        }
                        if (dpsType === 'Enum' && dpsValues?.range) {
                          return (
                            <div className="form-group">
                              <label>{t('triggers.setValue')} ({dps!.code})</label>
                              <select
                                value={String(action.params?.value ?? '')}
                                onChange={(e) => updateActionParams(i, { value: e.target.value })}
                              >
                                <option value="">{t('triggers.selectValue')}</option>
                                {dpsValues.range.map((v) => (
                                  <option key={v} value={v}>{v}</option>
                                ))}
                              </select>
                            </div>
                          );
                        }
                        if (dpsType === 'Boolean') {
                          return (
                            <div className="form-group">
                              <label>{t('triggers.setValue')} ({dps!.code})</label>
                              <select
                                value={String(action.params?.value ?? '')}
                                onChange={(e) => updateActionParams(i, { value: e.target.value === 'true' })}
                              >
                                <option value="">--</option>
                                <option value="true">{t('triggers.testOn')}</option>
                                <option value="false">{t('triggers.testOff')}</option>
                              </select>
                            </div>
                          );
                        }
                        return (
                          <div className="form-group">
                            <label>{t('triggers.setValue')}</label>
                            <input
                              type="text"
                              value={String(action.params?.value ?? '')}
                              onChange={(e) => updateActionParams(i, { value: e.target.value })}
                              placeholder={t('triggers.setValue')}
                            />
                          </div>
                        );
                      })()}
                    </>
                  )}
                </div>
              );
            })}
            <button
              type="button"
              className="add-action-btn"
              onClick={() => setActions([...actions, { action_type: 'tuya_on', device_id: null, dps_key: '1', params: {} }])}
            >
              {t('triggers.addAction')}
            </button>
          </div>

          <div className="form-section">
            <div className="form-group">
              <label>{t('triggers.cooldownSeconds')}</label>
              <input
                type="number"
                min="0"
                value={data.cooldown_seconds}
                onChange={(e) => setData({ ...data, cooldown_seconds: parseInt(e.target.value) || 0 })}
              />
            </div>
          </div>
          <div className="form-actions">
            <button onClick={onCancel} className="cancel-btn">{t('triggers.cancel')}</button>
            <button
              onClick={handleSave}
              className="save-btn"
              disabled={!data.name}
            >
              {t('triggers.save')}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

export default TriggerDashboard;
