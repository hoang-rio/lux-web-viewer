import { useState, useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { IModbusCategory, IModbusItem, IModbusStatus } from '../Intefaces';
import Loading from './Loading';
import * as logUtil from '../utils/logUtil';
import './ModbusDashboard.css';

interface ModbusDashboardProps {
  onClose: () => void;
}

type DisplayValue = number | string | boolean | null;

const ModbusDashboard = ({ onClose }: ModbusDashboardProps) => {
  const { t } = useTranslation();
  const [categories, setCategories] = useState<IModbusCategory[]>([]);
  const [status, setStatus] = useState<IModbusStatus | null>(null);
  const [values, setValues] = useState<Record<string, DisplayValue>>({});
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [openCategory, setOpenCategory] = useState<string | null>(null);
  const [advanced, setAdvanced] = useState(false);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [readState, setReadState] = useState<'loading' | 'success' | 'error'>('loading');
  const [noAccess, setNoAccess] = useState(false);
  const [savingKey, setSavingKey] = useState<string | null>(null);
  const [message, setMessage] = useState<{ text: string; type: 'success' | 'error' } | null>(null);
  const [pendingDanger, setPendingDanger] = useState<{ key: string; value: number | string } | null>(null);

  const apiBase = import.meta.env.VITE_API_BASE_URL;

  const fetchAll = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true);
    setReadState('loading');
    setValues({});
    setMessage(null);
    try {
      const regsRes = await fetch(`${apiBase}/modbus/registers`);
      if (!regsRes.ok) {
        setNoAccess(true);
        return;
      }
      const regsData = await regsRes.json();
      setCategories(regsData.categories || []);
      if (regsData.status) setStatus(regsData.status);
      const readRes = await fetch(`${apiBase}/modbus/read`);
      const readData = await readRes.json();
      if (readData.success && readData.values) {
        setValues(readData.values);
        setReadState('success');
      } else {
        setReadState('error');
        setMessage({ text: readData.message || t('modbus.readFailed'), type: 'error' });
      }
      if (readData.status) setStatus(readData.status);
    } catch (err) {
      logUtil.error('Failed to fetch modbus registers', err);
      setReadState('error');
      setMessage({ text: t('modbus.loadFailed'), type: 'error' });
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiBase, t]);

  useEffect(() => {
    fetchAll();
  }, [fetchAll]);

  const itemKind = (item: IModbusItem) => item.kind;

  const toggleItem = async (item: IModbusItem, checked: boolean) => {
    await applyWrite(item, checked ? 1 : 0, false);
  };

  const applyWrite = async (item: IModbusItem, value: number | string, confirm: boolean) => {
    if (savingKey) return;
    if (item.danger && !confirm) {
      setPendingDanger({ key: item.key, value });
      return;
    }
    setSavingKey(item.key);
    setMessage(null);
    try {
      const res = await fetch(`${apiBase}/modbus/write`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key: item.key, value, confirm: Boolean(confirm) }),
      });
      const data = await res.json();
      if (res.ok && data.success) {
        setValues((prev) => ({ ...prev, [item.key]: data.value }));
        setDrafts((prev) => {
          const next = { ...prev };
          delete next[item.key];
          return next;
        });
        setMessage({ text: t('modbus.writeSuccess'), type: 'success' });
      } else {
        setMessage({ text: data.message || t('modbus.writeFailed'), type: 'error' });
      }
    } catch (err) {
      logUtil.error('Modbus write failed', err);
      setMessage({ text: t('modbus.writeFailed'), type: 'error' });
    } finally {
      setSavingKey(null);
    }
  };

  const confirmDanger = async () => {
    if (!pendingDanger) return;
    const item = allItems().find((it) => it.key === pendingDanger.key);
    const { key, value } = pendingDanger;
    setPendingDanger(null);
    if (item) {
      await applyWrite(item, value, true);
    } else {
      logUtil.error('Pending danger item not found', key);
    }
  };

  const allItems = useCallback(() => {
    return categories.flatMap((c) => c.items);
  }, [categories]);

  const updateDraft = (key: string, value: string) => {
    setDrafts((prev) => ({ ...prev, [key]: value }));
  };

  const handleRefresh = async () => {
    setRefreshing(true);
    await fetchAll(true);
    setRefreshing(false);
  };

  const renderEditor = (item: IModbusItem) => {
    const current = values[item.key];
    const kind = itemKind(item);
    const editable = readState === 'success';
    const disabled = !status?.available || savingKey !== null || !editable;

    if (kind === 'toggle') {
      const checked = (current === 1 || current === true);
      return (
        <label className="modbus-switch">
          <input
            type="checkbox"
            checked={checked}
            disabled={disabled}
            aria-label={t(`modbus.reg.${item.key}`)}
            onChange={(e) => toggleItem(item, e.target.checked)}
          />
          <span className="modbus-switch-slider"></span>
        </label>
      );
    }

    if (kind === 'select' && item.options) {
      const value = drafts[item.key] ?? String(current ?? '');
      return (
        <select
          className="modbus-editor-select"
          value={value}
          disabled={disabled}
          onChange={(e) => updateDraft(item.key, e.target.value)}
        >
          {item.options.map((opt) => (
            <option key={opt.value} value={String(opt.value)}>
              {t(`modbus.opt.${item.key}.${opt.value}`)}
            </option>
          ))}
        </select>
      );
    }

    if (kind === 'time') {
      const value = drafts[item.key] ?? String(current ?? '00:00');
      return (
        <input
          type="time"
          className="modbus-editor-input"
          value={value}
          disabled={disabled}
          onChange={(e) => updateDraft(item.key, e.target.value)}
        />
      );
    }

    const numValue = drafts[item.key] ?? String(current ?? '');
    return (
      <input
        type="number"
        className="modbus-editor-input"
        value={numValue}
        min={item.min}
        max={item.max}
        step={item.scale ? String(item.scale) : '1'}
        disabled={disabled}
        onChange={(e) => updateDraft(item.key, e.target.value)}
      />
    );
  };

  const renderValue = (item: IModbusItem) => {
    const current = values[item.key];
    if (current === null || current === undefined) return '—';
    return String(current) + (item.unit ? ` ${item.unit}` : '');
  };

  const renderRow = (item: IModbusItem) => {
    if (item.danger && !advanced) return null;
    return (
      <div
        key={item.key}
        className={`modbus-item ${item.danger ? 'danger' : ''} ${savingKey === item.key ? 'saving' : ''}`}
      >
        {item.danger && (
          <span className="modbus-danger-mark" title={t('modbus.danger')}>⚠</span>
        )}
        <div className="modbus-item-info">
          <div className="modbus-item-name">
            {t(`modbus.reg.${item.key}`)}
            {item.unit && <span className="modbus-item-unit">{item.unit}</span>}
          </div>
          <div className="modbus-item-detail">
            <span className="modbus-item-current">
              {t('modbus.current')}: {renderValue(item)}
            </span>
            <span className="modbus-item-reg">R{item.reg}</span>
          </div>
          {item.verify && (
            <div className="modbus-verify-hint">{t('modbus.verifyHint')}</div>
          )}
        </div>
        <div className="modbus-item-editor">
          {renderEditor(item)}
          {itemKind(item) !== 'toggle' && (
            <button
              className="modbus-apply-btn"
              onClick={() => {
                const draft = drafts[item.key];
                if (draft === undefined || draft === '') {
                  setMessage({ text: t('modbus.valueRequired'), type: 'error' });
                  return;
                }
                const value = itemKind(item) === 'time' ? draft : Number(draft);
                applyWrite(item, value, item.danger);
              }}
              disabled={!status?.available || savingKey !== null || readState !== 'success' || (drafts[item.key] === undefined)}
            >
              {savingKey === item.key ? t('modbus.saving') : t('modbus.apply')}
            </button>
          )}
        </div>
      </div>
    );
  };

  if (noAccess) {
    return (
      <div className="modbus-dashboard-overlay">
        <div className="modbus-dashboard">
          <div className="modbus-dashboard-header">
            <h3>{t('modbus.title')}</h3>
            <button className="close-popover" onClick={onClose}>×</button>
          </div>
          <div className="modbus-no-access">
            <div className="modbus-no-access-icon">🔒</div>
            <p className="modbus-no-access-title">{t('modbus.noAccess')}</p>
            <p className="modbus-no-access-hint">{t('modbus.noAccessHint')}</p>
          </div>
        </div>
      </div>
    );
  }

  if (loading && categories.length === 0) {
    return (
      <div className="modbus-dashboard-overlay">
        <div className="modbus-dashboard">
          <div className="modbus-dashboard-header">
            <h3>{t('modbus.title')}</h3>
            <button className="close-popover" onClick={onClose}>×</button>
          </div>
          <Loading />
        </div>
      </div>
    );
  }


  return (
    <div className="modbus-dashboard-overlay">
      <div className="modbus-dashboard">
        <div className="modbus-dashboard-header">
          <h3>{t('modbus.title')}</h3>
          {message && (
            <div className={`modbus-message ${message.type}`}>{message.text}</div>
          )}
          <button
            className="modbus-refresh-btn"
            onClick={handleRefresh}
            title={t('modbus.refresh')}
            disabled={refreshing}
          >
            {refreshing ? '…' : '⟳'}
          </button>
          <button className="close-popover" onClick={onClose}>×</button>
        </div>
        <div className={`modbus-status-bar ${status?.available ? 'ok' : 'bad'}`}>
          <span className="modbus-status-dot"></span>
          {status?.available ? t('modbus.available') : t('modbus.unavailable')}
          <span className="modbus-status-mode">
            {t('modbus.mode')}: {status?.mode || '—'}
          </span>
        </div>
        <div className="modbus-advanced-row">
          <label className="modbus-advanced-label">
            <input
              type="checkbox"
              checked={advanced}
              onChange={(e) => setAdvanced(e.target.checked)}
            />
            {t('modbus.advanced')}
          </label>
          <span className="modbus-advanced-hint">{t('modbus.advancedHint')}</span>
        </div>
        <div className="modbus-categories">
          {(refreshing || (loading && categories.length > 0)) && (
            <div className="modbus-content-loading" role="status" aria-live="polite">
              <span className="modbus-content-loading-dot" />
              {t('modbus.loading')}
            </div>
          )}
          {!status?.available && (
            <div className="modbus-unavailable-note">{t('modbus.unavailableHint')}</div>
          )}
          {categories.length === 0 ? (
            <div className="no-records">{t('modbus.noRegisters')}</div>
          ) : (
            categories.map((cat) => {
              const visibleItems = cat.items.filter((item) => !item.danger || advanced);
              if (visibleItems.length === 0) return null;
              if (cat.items.length === 1) {
                return (
                  <div className="modbus-category" key={cat.key}>
                    {visibleItems.map((item) => renderRow(item))}
                  </div>
                );
              }
              const isCollapsed = openCategory !== cat.key;
              return (
                <div className="modbus-category" key={cat.key}>
                  <button
                    type="button"
                    className="modbus-category-heading"
                    onClick={() => setOpenCategory(isCollapsed ? cat.key : null)}
                    aria-expanded={!isCollapsed}
                    aria-controls={`modbus-category-${cat.key}`}
                  >
                    <span className={`modbus-category-caret ${isCollapsed ? 'collapsed' : ''}`}>
                      {isCollapsed ? '▸' : '▾'}
                    </span>
                    <span className="modbus-category-name">{t(`modbus.cat.${cat.key}`)}</span>
                  </button>
                  {!isCollapsed && (
                    <div
                      id={`modbus-category-${cat.key}`}
                      className="modbus-category-items"
                    >
                      {visibleItems.map((item) => renderRow(item))}
                    </div>
                  )}
                </div>
              );
            })
          )}
        </div>
            {pendingDanger && (
        <div className="modbus-confirm-overlay">
          <div className="modbus-confirm">
            <h4>{t('modbus.confirmTitle')}</h4>
            <p>{t('modbus.confirmDanger')}</p>
            <div className="modbus-confirm-actions">
              <button className="cancel-btn" onClick={() => setPendingDanger(null)}>
                {t('modbus.cancel')}
              </button>
              <button className="save-btn" onClick={confirmDanger}>
                {t('modbus.yes')}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
    </div>
  );
};

export default ModbusDashboard;
