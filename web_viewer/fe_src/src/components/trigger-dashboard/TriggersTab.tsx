import { useTranslation } from 'react-i18next';
import { ITrigger, ITriggerCondition, ITriggerAction, ITuyaDevice, IDeviceMapping, ITriggerHistory } from '../../Intefaces';
import { INVERTER_FIELDS, formatDays, resolveActions } from './constants';

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

export default function TriggersTab({ triggers, devices, deviceMappings, onAdd, onEdit, onDelete, onTest, onToggle, onShowHistory, historyTriggerId, triggerHistory, onCloseHistory }: TriggersTabProps) {
  const { t } = useTranslation();

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
        const { typeLabel, targetLabel } = resolveActionLabel(a);
        return targetLabel ? `${typeLabel} → ${targetLabel}` : typeLabel;
      }).join(' + ');
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
