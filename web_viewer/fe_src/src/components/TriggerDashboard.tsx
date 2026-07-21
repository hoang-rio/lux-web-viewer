import { useState, useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { ITuyaDevice, ITrigger, ITriggerCondition, ITriggerAction, IScannedDevice } from '../Intefaces';
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

const ACTION_TYPES = [
  { value: 'tuya_on', label: 'triggers.actionTurnOn' },
  { value: 'tuya_off', label: 'triggers.actionTurnOff' },
  { value: 'tuya_toggle', label: 'triggers.actionToggle' },
  { value: 'tuya_set', label: 'triggers.actionSetValue' },
  { value: 'notification', label: 'triggers.actionNotification' },
];

const DAY_KEYS = ['triggers.dayMon', 'triggers.dayTue', 'triggers.dayWed', 'triggers.dayThu', 'triggers.dayFri', 'triggers.daySat', 'triggers.daySun'];

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

  const fetchDeviceStatuses = useCallback(async (devs: ITuyaDevice[]) => {
    const statuses: Record<string, boolean> = {};
    await Promise.all(
      devs.map(async (d) => {
        try {
          const res = await fetch(`${import.meta.env.VITE_API_BASE_URL}/tuya-devices/${d.id}/status`, { method: 'POST' });
          const data = await res.json();
          if (data && data.dps) {
            statuses[d.id] = Boolean(data.dps['1']);
          } else if (data && data.dps === undefined && data.success !== undefined) {
            statuses[d.id] = Boolean(data.success);
          }
        } catch {
          statuses[d.id] = false;
        }
      })
    );
    setDeviceStatuses(statuses);
  }, []);

  useEffect(() => {
    const load = async () => {
      setLoading(true);
      await Promise.all([fetchDevices(), fetchTriggers(), fetchWizardStatus()]);
      setLoading(false);
    };
    load();
  }, [fetchDevices, fetchTriggers, fetchWizardStatus]);

  useEffect(() => {
    if (devices.length > 0) {
      fetchDeviceStatuses(devices);
    }
  }, [devices, fetchDeviceStatuses]);

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
              onAdd={() => setEditingTrigger(createEmptyTrigger())}
              onEdit={(t) => setEditingTrigger(t)}
              onDelete={handleDeleteTrigger}
              onTest={handleTestTrigger}
              onToggle={handleToggleTrigger}
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
    actions: [{ action_type: 'tuya_on', device_id: null, params: {} }],
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
          devices.map((d) => {
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
          })
        )}
      </div>
    </div>
  );
}

interface TriggersTabProps {
  triggers: ITrigger[];
  devices: ITuyaDevice[];
  onAdd: () => void;
  onEdit: (t: ITrigger) => void;
  onDelete: (id: number) => void;
  onTest: (id: number) => void;
  onToggle: (t: ITrigger) => void;
}

function TriggersTab({ triggers, devices, onAdd, onEdit, onDelete, onTest, onToggle }: TriggersTabProps) {
  const { t } = useTranslation();

  const resolveActions = (tr: ITrigger): ITriggerAction[] => {
    if (tr.actions && tr.actions.length > 0) return tr.actions;
    return [{ action_type: tr.action_type, device_id: tr.action_device_id, params: tr.action_params || {} }];
  };

  const resolveConditionLabel = (c: ITriggerCondition): string => {
    if (c.condition_type === 'device') {
      const dev = devices.find((d) => d.id === c.device_id);
      const devName = dev?.name || c.device_id;
      return `${devName} [${c.dps_key || '1'}] ${c.op} ${c.compare_value}`;
    }
    const found = INVERTER_FIELDS.find((f) => f.value === c.field);
    const fieldLabel = found ? t(found.label) : (c.field || '');
    return `${fieldLabel} ${c.op} ${c.value}`;
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
                    <strong>{t('triggers.when')}:</strong> {tr.when_start_time} - {tr.when_end_time}
                    {tr.when_days && ` (${tr.when_days})`}
                  </div>
                )}
                <div className="trigger-detail">
                  <strong>{t('triggers.if')}:</strong>{' '}
                  {tr.conditions.map((c, i) => (
                    <span key={i}>
                      {i > 0 && ' AND '}
                      {resolveConditionLabel(c)}
                    </span>
                  ))}
                </div>
                <div className="trigger-detail">
                  <strong>{t('triggers.then')}:</strong>{' '}
                  {actions.map((a, i) => {
                    const actionLabel = t(ACTION_TYPES.find((at) => at.value === a.action_type)?.label || a.action_type);
                    const dev = devices.find((d) => d.id === a.device_id);
                    return (
                      <span key={i}>
                        {i > 0 && ' + '}
                        {actionLabel}
                        {a.device_id && <> → {dev?.name || a.device_id}</>}
                        {a.action_type === 'notification' && a.params?.notification_title && (
                          <> - {a.params.notification_title}</>
                        )}
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
                <button onClick={() => onDelete(tr.id)} className="delete-btn">{t('triggers.delete')}</button>
              </div>
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
  onSave: (data: ITrigger) => void;
  onCancel: () => void;
}

function TriggerForm({ trigger, devices, onSave, onCancel }: TriggerFormProps) {
  const { t } = useTranslation();
  const [data, setData] = useState<ITrigger>({ ...trigger });
  const [conditions, setConditions] = useState<ITriggerCondition[]>(
    trigger.conditions.length > 0
      ? trigger.conditions.map((c) => ({ condition_type: 'inverter', ...c }))
      : [{ condition_type: 'inverter', field: 'soc', op: '>=', value: 100 }]
  );
  const [actions, setActions] = useState<ITriggerAction[]>(() => {
    if (trigger.actions && trigger.actions.length > 0) return [...trigger.actions];
    return [{ action_type: trigger.action_type || 'tuya_on', device_id: trigger.action_device_id, params: trigger.action_params || {} }];
  });
  const [selectedDays, setSelectedDays] = useState<number[]>(() => {
    if (!trigger.when_days) return [];
    return trigger.when_days.split(',').map(Number).filter(Boolean);
  });

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
                      onChange={(e) => updateCondition(i, { device_id: e.target.value })}
                      className="condition-device-select"
                    >
                      <option value="">{t('triggers.selectDevice')}</option>
                      {devices.map((d) => (
                        <option key={d.id} value={d.id}>{d.name}</option>
                      ))}
                    </select>
                    <input
                      type="text"
                      value={cond.dps_key || '1'}
                      onChange={(e) => updateCondition(i, { dps_key: e.target.value })}
                      className="condition-dps-key"
                      placeholder="DPS"
                    />
                    <select
                      value={cond.op}
                      onChange={(e) => updateCondition(i, { op: e.target.value })}
                    >
                      {CONDITION_OPS.map((op) => (
                        <option key={op} value={op}>{op}</option>
                      ))}
                    </select>
                    <input
                      type="text"
                      value={String(cond.compare_value ?? '')}
                      onChange={(e) => updateCondition(i, { compare_value: e.target.value })}
                      className="condition-value"
                      placeholder={t('triggers.condValue')}
                    />
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
            {actions.map((action, i) => (
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
                    onChange={(e) => updateAction(i, { action_type: e.target.value })}
                  >
                    {ACTION_TYPES.map((a) => (
                      <option key={a.value} value={a.value}>{t(a.label)}</option>
                    ))}
                  </select>
                </div>
                {action.action_type !== 'notification' && (
                  <div className="form-group">
                    <label>{t('triggers.device')}</label>
                    <select
                      value={action.device_id || ''}
                      onChange={(e) => updateAction(i, { device_id: e.target.value || null })}
                    >
                      <option value="">{t('triggers.selectDevice')}</option>
                      {devices.map((d) => (
                        <option key={d.id} value={d.id}>{d.name}</option>
                      ))}
                    </select>
                  </div>
                )}
                {action.action_type === 'notification' && (
                  <>
                    <div className="form-group">
                      <label>{t('triggers.notifTitle')}</label>
                      <input
                        type="text"
                        value={action.params?.notification_title || ''}
                        onChange={(e) => updateActionParams(i, { notification_title: e.target.value })}
                        placeholder={t('triggers.notifTitlePlaceholder')}
                      />
                    </div>
                    <div className="form-group">
                      <label>{t('triggers.notifBody')}</label>
                      <textarea
                        value={action.params?.notification_body || ''}
                        onChange={(e) => updateActionParams(i, { notification_body: e.target.value })}
                        placeholder={t('triggers.notifBodyPlaceholder')}
                        rows={2}
                      />
                    </div>
                  </>
                )}
              </div>
            ))}
            <button
              type="button"
              className="add-action-btn"
              onClick={() => setActions([...actions, { action_type: 'tuya_on', device_id: null, params: {} }])}
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
