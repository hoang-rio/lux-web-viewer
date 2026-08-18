import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ITuyaDevice, IScannedDevice } from '../../Intefaces';

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
  onRename: (id: string, name: string) => void;
  onAddDevice: () => void;
}

export default function DevicesTab({ devices, scannedDevices, scanning, wizardRun, deviceStatuses, onScan, onRegister, onDelete, onTest, onRename }: DevicesTabProps) {
  const { t } = useTranslation();
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const registeredIds = new Set(devices.map((d) => d.id));
  const filteredScanned = scannedDevices.filter((s) => !registeredIds.has(s.gwId));

  const startRename = (d: ITuyaDevice) => {
    setRenamingId(d.id);
    setRenameValue(d.name);
  };

  const handleRenameKeyDown = (e: React.KeyboardEvent, id: string) => {
    if (e.key === 'Enter') {
      const trimmed = renameValue.trim();
      if (trimmed) {
        onRename(id, trimmed);
        setRenamingId(null);
      }
    } else if (e.key === 'Escape') {
      setRenamingId(null);
    }
  };

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
                  {renamingId === d.id ? (
                    <input
                      type="text"
                      className="rename-input"
                      value={renameValue}
                      onChange={(e) => setRenameValue(e.target.value)}
                      onKeyDown={(e) => handleRenameKeyDown(e, d.id)}
                      onBlur={() => setRenamingId(null)}
                      autoFocus
                    />
                  ) : (
                    <span className="device-name">{d.name}</span>
                  )}
                  <span className="device-detail">ID: {d.id}</span>
                  <span className="device-detail">IP: {d.ip}</span>
                </div>
                <div className="device-actions">
                  {renamingId === d.id ? (
                    <>
                      <button
                        onClick={() => {
                          const trimmed = renameValue.trim();
                          if (trimmed) {
                            onRename(d.id, trimmed);
                            setRenamingId(null);
                          }
                        }}
                        className="edit-btn"
                        disabled={!renameValue.trim()}
                      >
                        {t('triggers.save')}
                      </button>
                      <button onClick={() => setRenamingId(null)} className="delete-btn">
                        {t('triggers.cancel')}
                      </button>
                    </>
                  ) : (
                    <>
                      <button onClick={() => startRename(d)} className="edit-btn">
                        {t('triggers.rename')}
                      </button>
                      <button
                        onClick={() => onTest(d.id, isOn ? 'turn_off' : 'turn_on')}
                        className={`test-btn ${isOn ? 'off' : 'on'}`}
                      >
                        {isOn ? t('triggers.testOff') : t('triggers.testOn')}
                      </button>
                      <button onClick={() => onDelete(d.id)} className="delete-btn">
                        {t('triggers.delete')}
                      </button>
                    </>
                  )}
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
