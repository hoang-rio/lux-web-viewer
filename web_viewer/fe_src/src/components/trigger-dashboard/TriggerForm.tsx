import { useState, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { ITrigger, ITriggerCondition, ITriggerAction, ITuyaDevice, IDeviceMapping } from '../../Intefaces';
import { INVERTER_FIELDS, CONDITION_OPS, DAY_KEYS, getDeviceDpsList, getActionTypeOptions } from './constants';

interface TriggerFormProps {
  trigger: ITrigger;
  devices: ITuyaDevice[];
  deviceMappings: Record<string, IDeviceMapping>;
  castConfigured: boolean;
  onSave: (data: ITrigger) => void;
  onCancel: () => void;
}

export default function TriggerForm({ trigger, devices, deviceMappings, castConfigured, onSave, onCancel }: TriggerFormProps) {
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
                <span className="condition-row-number">#{i + 1}</span>
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
              const actionOptions = getActionTypeOptions(castConfigured);

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
