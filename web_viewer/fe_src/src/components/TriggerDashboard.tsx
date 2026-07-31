import { useState, useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { ITuyaDevice, ITrigger, IScannedDevice, IDeviceMapping, ITriggerHistory } from '../Intefaces';
import Loading from './Loading';
import DevicesTab from './trigger-dashboard/DevicesTab';
import TriggersTab from './trigger-dashboard/TriggersTab';
import TriggerForm from './trigger-dashboard/TriggerForm';
import { createEmptyTrigger } from './trigger-dashboard/constants';
import * as logUtil from '../utils/logUtil';
import './TriggerDashboard.css';

interface TriggerDashboardProps {
  onClose: () => void;
}

export default function TriggerDashboard({ onClose }: TriggerDashboardProps) {
  const { t } = useTranslation();
  const [activeTab, setActiveTab] = useState<'devices' | 'triggers'>('triggers');
  const [devices, setDevices] = useState<ITuyaDevice[]>([]);
  const [triggers, setTriggers] = useState<ITrigger[]>([]);
  const [scannedDevices, setScannedDevices] = useState<IScannedDevice[]>([]);
  const [loading, setLoading] = useState(true);
  const [noAccess, setNoAccess] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [editingTrigger, setEditingTrigger] = useState<ITrigger | null>(null);
  const [showAddDevice, setShowAddDevice] = useState(false);
  const [message, setMessage] = useState<{ text: string; type: 'success' | 'error' } | null>(null);
  const [wizardRun, setWizardRun] = useState<boolean>(true);
  const [deviceStatuses, setDeviceStatuses] = useState<Record<string, boolean>>({});
  const [deviceMappings, setDeviceMappings] = useState<Record<string, IDeviceMapping>>({});
  const [historyTriggerId, setHistoryTriggerId] = useState<number | null>(null);
  const [triggerHistory, setTriggerHistory] = useState<ITriggerHistory[]>([]);
  const [castConfigured, setCastConfigured] = useState(false);

  const fetchWizardStatus = useCallback(async () => {
    try {
      const res = await fetch(`${import.meta.env.VITE_API_BASE_URL}/tuya-devices/wizard-status`);
      if (!res.ok) {
        setNoAccess(true);
        return;
      }
      const data = await res.json();
      setWizardRun(Boolean(data.wizard_run));
    } catch (err) {
      logUtil.error('Failed to fetch wizard status', err);
    }
  }, []);

  const fetchDevices = useCallback(async () => {
    try {
      const res = await fetch(`${import.meta.env.VITE_API_BASE_URL}/tuya-devices`);
      if (!res.ok) {
        setNoAccess(true);
        return;
      }
      const data = await res.json();
      setDevices(data.devices || []);
    } catch (err) {
      logUtil.error('Failed to fetch devices', err);
    }
  }, []);

  const fetchTriggers = useCallback(async () => {
    try {
      const res = await fetch(`${import.meta.env.VITE_API_BASE_URL}/triggers`);
      if (!res.ok) {
        setNoAccess(true);
        return;
      }
      const data = await res.json();
      setTriggers(data.triggers || []);
    } catch (err) {
      logUtil.error('Failed to fetch triggers', err);
    }
  }, []);

  const fetchDeviceMappings = useCallback(async () => {
    try {
      const res = await fetch(`${import.meta.env.VITE_API_BASE_URL}/tuya-devices/mappings`);
      if (!res.ok) {
        setNoAccess(true);
        return;
      }
      const data = await res.json();
      setDeviceMappings(data.mappings || {});
    } catch (err) {
      logUtil.error('Failed to fetch device mappings', err);
    }
  }, []);

  const fetchCastStatus = useCallback(async () => {
    try {
      const res = await fetch(`${import.meta.env.VITE_API_BASE_URL}/settings`);
      if (!res.ok) {
        setNoAccess(true);
        return;
      }
      const data = await res.json();
      setCastConfigured(data.CAST_DEVICE_CONFIGURED === 'true');
    } catch (err) {
      logUtil.error('Failed to fetch cast status', err);
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
      if (!res.ok) {
        setNoAccess(true);
        return;
      }
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
      await Promise.all([fetchDevices(), fetchTriggers(), fetchWizardStatus(), fetchDeviceMappings(), fetchCastStatus()]);
      setLoading(false);
    };
    load();
  }, [fetchDevices, fetchTriggers, fetchWizardStatus, fetchDeviceMappings, fetchCastStatus]);

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
    if (noAccess) return;
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
    if (noAccess) return;
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
    if (noAccess) return;
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
    if (noAccess) return;
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
    if (noAccess) return;
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
    if (noAccess) return;
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
    if (noAccess) return;
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
    if (noAccess) return;
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

  if (noAccess) {
    return (
      <div className="trigger-dashboard-overlay">
        <div className="trigger-dashboard">
          <div className="trigger-dashboard-header">
            <h3>{t('triggers.title')}</h3>
            <button className="close-popover" onClick={onClose}>×</button>
          </div>
          <div className="trigger-no-access">
            <div className="trigger-no-access-icon">🔒</div>
            <p className="trigger-no-access-title">{t('triggers.noAddPermission')}</p>
            <p className="trigger-no-access-hint">{t('triggers.noAddPermissionHint')}</p>
          </div>
        </div>
      </div>
    );
  }

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
        castConfigured={castConfigured}
        onSave={async (data) => {
          if (noAccess) return;
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
              onAddDevice={() => { if (noAccess) return; setShowAddDevice(!showAddDevice); }}
            />
          )}
          {activeTab === 'triggers' && (
            <TriggersTab
              triggers={triggers}
              devices={devices}
              deviceMappings={deviceMappings}
              onAdd={() => { if (noAccess) return; setEditingTrigger(createEmptyTrigger()); }}
              onEdit={(t) => { if (noAccess) return; setEditingTrigger(t); }}
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
