import { useState, useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { ITuyaDevice, ITrigger, ITriggerCondition, IScannedDevice } from '../Intefaces';
import Loading from './Loading';
import * as logUtil from '../utils/logUtil';
import './TriggerDashboard.css';

interface TriggerDashboardProps {
  onClose: () => void;
}

const CONDITION_FIELDS = [
  { value: 'soc', label: 'SOC (%)' },
  { value: 'p_pv', label: 'PV Power (W)' },
  { value: 'p_discharge', label: 'Discharge Power (W)' },
  { value: 'p_charge', label: 'Charge Power (W)' },
  { value: 'fac', label: 'Grid Frequency (Hz)' },
  { value: 'p_eps', label: 'EPS Power (W)' },
  { value: 'p_to_grid', label: 'To Grid (W)' },
  { value: 'p_to_user', label: 'To User (W)' },
  { value: 'v_bat', label: 'Battery Voltage (V)' },
];

const CONDITION_OPS = ['>', '<', '>=', '<=', '==', '!='];

const ACTION_TYPES = [
  { value: 'tuya_on', label: 'Turn On Device' },
  { value: 'tuya_off', label: 'Turn Off Device' },
  { value: 'tuya_toggle', label: 'Toggle Device' },
  { value: 'tuya_set', label: 'Set Device Value' },
  { value: 'notification', label: 'Send Notification' },
];

const DAY_NAMES = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

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

  useEffect(() => {
    const load = async () => {
      setLoading(true);
      await Promise.all([fetchDevices(), fetchTriggers()]);
      setLoading(false);
    };
    load();
  }, [fetchDevices, fetchTriggers]);

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
    const localKey = prompt(t('triggers.enterLocalKey'));
    if (!localKey) return;
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
    conditions: [{ field: 'soc', op: '>=', value: 100 }],
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
  onScan: () => void;
  onRegister: (d: IScannedDevice) => void;
  onDelete: (id: string) => void;
  onTest: (id: string, action: 'turn_on' | 'turn_off') => void;
  onAddDevice: () => void;
}

function DevicesTab({ devices, scannedDevices, scanning, onScan, onRegister, onDelete, onTest }: DevicesTabProps) {
  const { t } = useTranslation();
  return (
    <div className="devices-tab">
      <div className="devices-actions">
        <button onClick={onScan} disabled={scanning} className="scan-btn">
          {scanning ? t('triggers.scanning') : t('triggers.scanForDevices')}
        </button>
      </div>
      {scannedDevices.length > 0 && (
        <div className="devices-section">
          <h4>{t('triggers.discoveredDevices')}</h4>
          {scannedDevices.map((d, i) => (
            <div key={i} className="device-card discovered">
              <div className="device-info">
                <span className="device-name">{d.name || t('triggers.unknownDevice')}</span>
                <span className="device-detail">ID: {d.gwId}</span>
                <span className="device-detail">IP: {d.ip}</span>
                <span className="device-detail">v{d.version}</span>
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
          devices.map((d) => (
            <div key={d.id} className="device-card registered">
              <div className="device-info">
                <span className="device-name">{d.name}</span>
                <span className="device-detail">ID: {d.id}</span>
                <span className="device-detail">IP: {d.ip}</span>
              </div>
              <div className="device-actions">
                <button onClick={() => onTest(d.id, 'turn_on')} className="test-btn on">
                  {t('triggers.testOn')}
                </button>
                <button onClick={() => onTest(d.id, 'turn_off')} className="test-btn off">
                  {t('triggers.testOff')}
                </button>
                <button onClick={() => onDelete(d.id)} className="delete-btn">
                  {t('triggers.delete')}
                </button>
              </div>
            </div>
          ))
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
        triggers.map((tr) => (
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
                    {CONDITION_FIELDS.find((f) => f.value === c.field)?.label || c.field} {c.op} {c.value}
                  </span>
                ))}
              </div>
              <div className="trigger-detail">
                <strong>{t('triggers.then')}:</strong>{' '}
                {ACTION_TYPES.find((a) => a.value === tr.action_type)?.label || tr.action_type}
                {tr.action_device_id && (
                  <> → {devices.find((d) => d.id === tr.action_device_id)?.name || tr.action_device_id}</>
                )}
                {tr.action_type === 'notification' && tr.action_params && (
                  <> - {tr.action_params.notification_title || ''}</>
                )}
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
        ))
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
    trigger.conditions.length > 0 ? [...trigger.conditions] : [{ field: 'soc', op: '>=', value: 100 }]
  );
  const [selectedDays, setSelectedDays] = useState<number[]>(() => {
    if (!trigger.when_days) return [];
    return trigger.when_days.split(',').map(Number).filter(Boolean);
  });
  const [notifTitle, setNotifTitle] = useState(trigger.action_params?.notification_title || '');
  const [notifBody, setNotifBody] = useState(trigger.action_params?.notification_body || '');

  const toggleDay = (day: number) => {
    setSelectedDays((prev) =>
      prev.includes(day) ? prev.filter((d) => d !== day) : [...prev, day]
    );
  };

  const handleSave = () => {
    const payload: ITrigger = {
      ...data,
      conditions,
      when_days: selectedDays.length > 0 ? selectedDays.join(',') : null,
      when_start_time: data.when_start_time || null,
      when_end_time: data.when_end_time || null,
      action_device_id: data.action_type === 'notification' ? null : data.action_device_id,
      action_params: data.action_type === 'notification'
        ? { notification_title: notifTitle, notification_body: notifBody }
        : data.action_params,
    };
    onSave(payload);
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
                {DAY_NAMES.map((name, i) => (
                  <button
                    key={i}
                    type="button"
                    className={`day-btn ${selectedDays.includes(i + 1) ? 'selected' : ''}`}
                    onClick={() => toggleDay(i + 1)}
                  >
                    {name}
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
                  value={cond.field}
                  onChange={(e) => {
                    const newConds = [...conditions];
                    newConds[i] = { ...cond, field: e.target.value };
                    setConditions(newConds);
                  }}
                >
                  {CONDITION_FIELDS.map((f) => (
                    <option key={f.value} value={f.value}>{f.label}</option>
                  ))}
                </select>
                <select
                  value={cond.op}
                  onChange={(e) => {
                    const newConds = [...conditions];
                    newConds[i] = { ...cond, op: e.target.value };
                    setConditions(newConds);
                  }}
                >
                  {CONDITION_OPS.map((op) => (
                    <option key={op} value={op}>{op}</option>
                  ))}
                </select>
                <input
                  type="number"
                  value={cond.value}
                  onChange={(e) => {
                    const newConds = [...conditions];
                    newConds[i] = { ...cond, value: parseFloat(e.target.value) || 0 };
                    setConditions(newConds);
                  }}
                  className="condition-value"
                />
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
              onClick={() => setConditions([...conditions, { field: 'soc', op: '>=', value: 100 }])}
            >
              {t('triggers.addCondition')}
            </button>
          </div>
          <div className="form-section">
            <h4>{t('triggers.actionSection')}</h4>
            <div className="form-group">
              <label>{t('triggers.actionType')}</label>
              <select
                value={data.action_type}
                onChange={(e) => setData({ ...data, action_type: e.target.value })}
              >
                {ACTION_TYPES.map((a) => (
                  <option key={a.value} value={a.value}>{a.label}</option>
                ))}
              </select>
            </div>
            {data.action_type !== 'notification' && (
              <div className="form-group">
                <label>{t('triggers.device')}</label>
                <select
                  value={data.action_device_id || ''}
                  onChange={(e) => setData({ ...data, action_device_id: e.target.value || null })}
                >
                  <option value="">{t('triggers.selectDevice')}</option>
                  {devices.map((d) => (
                    <option key={d.id} value={d.id}>{d.name}</option>
                  ))}
                </select>
              </div>
            )}
            {data.action_type === 'notification' && (
              <>
                <div className="form-group">
                  <label>{t('triggers.notifTitle')}</label>
                  <input
                    type="text"
                    value={notifTitle}
                    onChange={(e) => setNotifTitle(e.target.value)}
                    placeholder={t('triggers.notifTitlePlaceholder')}
                  />
                </div>
                <div className="form-group">
                  <label>{t('triggers.notifBody')}</label>
                  <textarea
                    value={notifBody}
                    onChange={(e) => setNotifBody(e.target.value)}
                    placeholder={t('triggers.notifBodyPlaceholder')}
                    rows={3}
                  />
                </div>
              </>
            )}
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
