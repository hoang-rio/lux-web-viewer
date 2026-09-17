import { useTranslation } from 'react-i18next';
import { useState, useEffect, forwardRef, useRef } from 'react';
import Loading from './Loading';
import * as logUtil from '../utils/logUtil';
import { apiFetch, apiGetJsonOrThrow } from '../utils/fetchUtil';
import './SettingsPopover.css';

interface SettingsPopoverProps {
  onClose: () => void;
  onOpenModbus: () => void;
}

interface Settings {
  SLEEP_TIME: string;
  ABNORMAL_DETECTION_ENABLED: string;
  ABNORMAL_CHECK_COOLDOWN_HOURS: string;
  ABNORMAL_MIN_POWER: string;
  OFF_GRID_WARNING_POWER: string;
  OFF_GRID_WARNING_SOC: string;
  MAX_BATTERY_POWER: string;
  OFF_GRID_WARNING_ENABLED: string;
  GRID_STATE_NOTIFY_ENABLED: string;
  BATTERY_FULL_NOTIFY_ENABLED: string;
  BATTERY_FULL_NOTIFY_BODY: string;
  ABNORMAL_NOTIFY_BODY: string;
  AUTH_ENABLED: string;
  AUTH_USERNAME: string;
  AUTH_PASSWORD: string;
  AUTH_BYPASS_CIDR: string;
}

const SLEEP_TIME_OPTIONS = ["3", "5", "10", "15", "30"];

let settingsBootstrapInFlight: Promise<{ pgMode: boolean; data: Record<string, string> }> | null = null;

const fetchSettingsBootstrap = async (): Promise<{ pgMode: boolean; data: Record<string, string> }> => {
  if (!settingsBootstrapInFlight) {
    settingsBootstrapInFlight = (async () => {
      const authConfigRes = await apiFetch('/auth/config');
      const authConfig = await authConfigRes.json();
      const pgMode = Boolean(authConfig?.auth_required);

      const data = await apiGetJsonOrThrow<Record<string, string>>('/settings', {
        withAuth: pgMode,
      });

      return { pgMode, data };
    })();
  }

  try {
    return await settingsBootstrapInFlight;
  } finally {
    settingsBootstrapInFlight = null;
  }
};

const SettingsPopover = forwardRef<HTMLDivElement, SettingsPopoverProps>(({ onClose, onOpenModbus }, ref) => {
  const { t } = useTranslation();
  const isMountedRef = useRef(true);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [originalSettings, setOriginalSettings] = useState<Settings | null>(null);
  const [isPgMode, setIsPgMode] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<{text: string, type: 'success' | 'error'} | null>(null);
  const [passwordConfirm, setPasswordConfirm] = useState<string>('');
  const [cidrError, setCidrError] = useState<string | null>(null);

  useEffect(() => {
    isMountedRef.current = true;
    fetchSettings();
    return () => {
      isMountedRef.current = false;
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const fetchSettings = async () => {
    try {
      const { pgMode, data } = await fetchSettingsBootstrap();
      setIsPgMode(pgMode);
      // Set defaults if missing
      const defaults: Settings = {
        SLEEP_TIME: '30',
        ABNORMAL_DETECTION_ENABLED: 'true',
        ABNORMAL_CHECK_COOLDOWN_HOURS: '3',
        ABNORMAL_MIN_POWER: '900',
        OFF_GRID_WARNING_POWER: '2200',
        OFF_GRID_WARNING_SOC: '87',
        MAX_BATTERY_POWER: '3000',
        OFF_GRID_WARNING_ENABLED: 'true',
        GRID_STATE_NOTIFY_ENABLED: 'true',
        BATTERY_FULL_NOTIFY_ENABLED: 'true',
        BATTERY_FULL_NOTIFY_BODY: 'Pin đã sạc đầy 100%. Có thể bật bình nóng lạnh để tối ưu sử dụng.',
        ABNORMAL_NOTIFY_BODY: 'Tiêu thụ điện bất thường, vui lòng kiểm tra xem vòi nước đã khoá chưa.'
        ,AUTH_ENABLED: pgMode ? '' : 'false'
        ,AUTH_USERNAME: pgMode ? '' : 'admin'
        ,AUTH_PASSWORD: pgMode ? '' : 'changeme'
        ,AUTH_BYPASS_CIDR: pgMode ? '' : '127.0.0.1/32,::1/128'
      };
      const merged = { ...defaults, ...data };
      merged.SLEEP_TIME = SLEEP_TIME_OPTIONS.includes(String(merged.SLEEP_TIME))
        ? String(merged.SLEEP_TIME)
        : defaults.SLEEP_TIME;
      if (!isMountedRef.current) {
        return;
      }
      setSettings(merged);
      setOriginalSettings(merged);
      // If auth already enabled in saved settings, pre-fill confirm password
      if (!pgMode && merged.AUTH_ENABLED === 'true') {
        setPasswordConfirm(merged.AUTH_PASSWORD || '');
      }
    } catch (err) {
      logUtil.error(t('settings.fetchError'), err);
      if (!isMountedRef.current) {
        return;
      }
      setMessage({text: t('settings.saveError'), type: 'error'});
    } finally {
      if (isMountedRef.current) {
        setLoading(false);
      }
    }
  };

  const handleSave = async () => {
    if (!settings) return;
    setSaving(true);
    setMessage(null);
    // If auth enabled, ensure username and password present and confirmation match
    if (!isPgMode && settings.AUTH_ENABLED === 'true') {
      if (!settings.AUTH_USERNAME || settings.AUTH_USERNAME.trim() === '') {
        setMessage({text: t('settings.authUsernameRequired'), type: 'error'});
        setSaving(false);
        return;
      }
      if (!settings.AUTH_PASSWORD || settings.AUTH_PASSWORD === '') {
        setMessage({text: t('settings.authPasswordRequired'), type: 'error'});
        setSaving(false);
        return;
      }
      if (settings.AUTH_PASSWORD !== passwordConfirm) {
        setMessage({text: t('settings.authPasswordMismatch'), type: 'error'});
        setSaving(false);
        return;
      }
    }
    try {
      // Do not send password confirmation to backend
      const payload = { ...settings } as unknown as Record<string, string>;
      delete payload.AUTH_PASSWORD_CONFIRM;
      if (isPgMode) {
        delete payload.AUTH_ENABLED;
        delete payload.AUTH_USERNAME;
        delete payload.AUTH_PASSWORD;
        delete payload.AUTH_BYPASS_CIDR;
      }
      const data = await apiGetJsonOrThrow<{ success?: boolean }>('/settings', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        withAuth: isPgMode,
        body: JSON.stringify(payload),
      });
      if (data.success) {
        const prevAuthEnabled = !isPgMode && originalSettings ? originalSettings.AUTH_ENABLED === 'true' : false;
        setOriginalSettings(settings);
        setMessage({text: t('settings.saveSuccess'), type: 'success'});
        // If auth changed from disabled -> enabled, reload page to ensure auth middleware and client state take effect
        if (!isPgMode && !prevAuthEnabled && settings.AUTH_ENABLED === 'true') {
          // small delay to allow UI message to show briefly
          setTimeout(() => window.location.reload(), 300);
        }
      } else {
        setMessage({text: t('settings.saveError'), type: 'error'});
      }
    } catch (err) {
      logUtil.error(t('settings.saveError'), err);
      setMessage({text: t('settings.saveError'), type: 'error'});
    } finally {
      setSaving(false);
    }
  };

  const updateSetting = (key: keyof Settings, value: string) => {
    if (settings) {
      // If enabling auth, reset password fields so user must re-enter
      if (!isPgMode && key === 'AUTH_ENABLED' && value === 'true' && settings.AUTH_ENABLED !== 'true') {
        // When enabling auth from disabled state, require re-entering the password
        // but keep the existing username so user doesn't have to retype it.
        setPasswordConfirm('');
        setSettings({ ...settings, AUTH_ENABLED: value, AUTH_PASSWORD: '' });
        return;
      }
      setSettings({ ...settings, [key]: value });
    }
  };

  const validateCidrList = (value: string) => {
    if (!value || value.trim() === '') return null;
    const items = value.split(",").map(s => s.trim()).filter(Boolean);
    const ipv4Regex = /^(\d{1,3}(?:\.\d{1,3}){3})\/(\d{1,2})$/;
    const ipv6Regex = /^([0-9A-Fa-f:]+)\/(\d{1,3})$/;
    for (const it of items) {
      let m = it.match(ipv4Regex);
      if (m) {
        const octets = m[1].split('.').map(Number);
        if (octets.some(o => Number.isNaN(o) || o < 0 || o > 255)) return t('settings.cidrInvalidIPv4Address', { cidr: it });
        const prefix = parseInt(m[2], 10);
        if (isNaN(prefix) || prefix < 0 || prefix > 32) return t('settings.cidrInvalidIPv4Prefix', { cidr: it });
        continue;
      }
      m = it.match(ipv6Regex);
      if (m) {
        const prefix = parseInt(m[2], 10);
        if (isNaN(prefix) || prefix < 0 || prefix > 128) return t('settings.cidrInvalidIPv6Prefix', { cidr: it });
        continue;
      }
      return t('settings.cidrInvalidEntry', { cidr: it });
    }
    return null;
  };

  const hasChanges = () => {
    if (!settings || !originalSettings) return false;
    return Object.keys(settings).some((key) => settings[key as keyof Settings] !== originalSettings[key as keyof Settings]);
  };

  if (loading) {
    return (
      <div className="settings-popover" ref={ref}>
        <div className="notification-popover-content">
          <div className="notification-popover-header">
            <h3>{t('settings.title')}</h3>
            <button className="close-popover" onClick={onClose}>
              ×
            </button>
          </div>
          <Loading />
        </div>
      </div>
    );
  }

  if (!settings) {
    return null;
  }

  return (
    <div className="settings-popover" ref={ref}>
      <div className="notification-popover-content">
        <div className="notification-popover-header">
          <h3>{t("settings.title")}</h3>
          <div className="settings-header-actions">
            <button
              className="triggers-dashboard-btn"
              onClick={onOpenModbus}
              title={t("modbus.title")}
            >
              {/* Cpu icon SVG */}
              <svg
                xmlns="http://www.w3.org/2000/svg"
                width="18"
                height="18"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
                viewBox="0 0 24 24"
                className="feather feather-cpu"
              >
                <rect x="4" y="4" width="16" height="16" rx="2" ry="2"></rect>
                <rect x="9" y="9" width="6" height="6"></rect>
                <line x1="9" y1="1" x2="9" y2="4"></line>
                <line x1="15" y1="1" x2="15" y2="4"></line>
                <line x1="9" y1="20" x2="9" y2="23"></line>
                <line x1="15" y1="20" x2="15" y2="23"></line>
                <line x1="20" y1="9" x2="23" y2="9"></line>
                <line x1="20" y1="14" x2="23" y2="14"></line>
                <line x1="1" y1="9" x2="4" y2="9"></line>
                <line x1="1" y1="14" x2="4" y2="14"></line>
              </svg>
            </button>
          </div>
          {message && (
            <div className={`settings-message ${message.type}`}>
              {message.text}
            </div>
          )}
          <button className="close-popover" onClick={onClose}>
            ×
          </button>
        </div>
        <div className="settings-content">
          <div className="settings-section">
            <h4>{t("settings.inverterSurveySection")}</h4>
            <div className="setting-item">
              <label>{t("settings.inverterSurveyInterval")}</label>
              <select
                value={settings.SLEEP_TIME}
                onChange={(e) => updateSetting("SLEEP_TIME", e.target.value)}
              >
                {SLEEP_TIME_OPTIONS.map((value) => (
                  <option key={value} value={value}>
                    {`${value} ${t("settings.seconds")}`}
                  </option>
                ))}
              </select>
            </div>
            <p className="setting-description">{t("settings.inverterSurveyIntervalDescription")}</p>
          </div>
          {isPgMode && (
          <div className="settings-section">
            <h4>{t("settings.gridSettings")}</h4>
            <div className="setting-item">
              <label>
                <input
                  type="checkbox"
                  checked={settings.GRID_STATE_NOTIFY_ENABLED === "true"}
                  onChange={(e) =>
                    updateSetting(
                      "GRID_STATE_NOTIFY_ENABLED",
                      e.target.checked ? "true" : "false"
                    )
                  }
                />
                {t("settings.gridStateNotifyEnabled")}
              </label>
            </div>
          </div>
          )}
          <div className="settings-section">
            <h4>{t("settings.batterySection")}</h4>
            <div className="setting-item">
              <label>
                <input
                  type="checkbox"
                  checked={settings.BATTERY_FULL_NOTIFY_ENABLED === "true"}
                  onChange={(e) =>
                    updateSetting(
                      "BATTERY_FULL_NOTIFY_ENABLED",
                      e.target.checked ? "true" : "false"
                    )
                  }
                />
                {t("settings.batteryFullNotifyEnabled")}
              </label>
            </div>
            <div className="setting-item setting-item-block">
              <label>{t("settings.notifyBody")}</label>
              <textarea
                value={settings.BATTERY_FULL_NOTIFY_BODY}
                onChange={(e) =>
                  updateSetting("BATTERY_FULL_NOTIFY_BODY", e.target.value)
                }
                disabled={settings.BATTERY_FULL_NOTIFY_ENABLED !== "true"}
                rows={3}
              />
            </div>
          </div>          <div className="settings-section">
            <h4>{t("settings.abnormalUsageSection")}</h4>
            <div className="setting-item">
              <label>
                <input
                  type="checkbox"
                  checked={settings.ABNORMAL_DETECTION_ENABLED === "true"}
                  onChange={(e) =>
                    updateSetting(
                      "ABNORMAL_DETECTION_ENABLED",
                      e.target.checked ? "true" : "false"
                    )
                  }
                />
                {t("settings.abnormalDetectionEnabled")}
              </label>
            </div>
            <div className="setting-item">
              <label>{t("settings.abnormalSkipCheckHours")}</label>
              <input
                type="number"
                min="1"
                max="5"
                value={settings.ABNORMAL_CHECK_COOLDOWN_HOURS}
                onChange={(e) =>
                  updateSetting("ABNORMAL_CHECK_COOLDOWN_HOURS", e.target.value)
                }
                disabled={settings.ABNORMAL_DETECTION_ENABLED !== "true"}
              />
              <span>{t("settings.hours")}</span>
            </div>
            <div className="setting-item">
              <label>{t("settings.abnormalMinPower")}</label>
              <input
                type="number"
                min="500"
                max="1500"
                value={settings.ABNORMAL_MIN_POWER}
                onChange={(e) =>
                  updateSetting("ABNORMAL_MIN_POWER", e.target.value)
                }
                disabled={settings.ABNORMAL_DETECTION_ENABLED !== "true"}
              />
              <span>{t("settings.watts")}</span>
            </div>
            <div className="setting-item setting-item-block">
              <label>{t("settings.notifyBody")}</label>
              <textarea
                value={settings.ABNORMAL_NOTIFY_BODY}
                onChange={(e) =>
                  updateSetting("ABNORMAL_NOTIFY_BODY", e.target.value)
                }
                disabled={settings.ABNORMAL_DETECTION_ENABLED !== "true"}
                rows={3}
              />
            </div>
          </div>
          {!isPgMode && (
          <div className="settings-section">
            <h4>{t("settings.authSection")}</h4>
            <div className="setting-item">
              <label>
                <input
                  type="checkbox"
                  checked={settings.AUTH_ENABLED === "true"}
                  onChange={(e) =>
                    updateSetting(
                      "AUTH_ENABLED",
                      e.target.checked ? "true" : "false"
                    )
                  }
                />
                {t("settings.authEnabled")}
              </label>
            </div>
            <p className="setting-description">
              {t("settings.authDescription")}
            </p>
            <div className="setting-item">
              <label>{t("settings.authUsername")}</label>
              <input
                type="text"
                value={settings.AUTH_USERNAME}
                onChange={(e) => updateSetting("AUTH_USERNAME", e.target.value)}
                disabled={settings.AUTH_ENABLED !== "true"}
              />
            </div>
            <div className="setting-item">
              <label>{t("settings.authPassword")}</label>
              <input
                type="password"
                value={settings.AUTH_PASSWORD}
                onChange={(e) => updateSetting("AUTH_PASSWORD", e.target.value)}
                disabled={settings.AUTH_ENABLED !== "true"}
              />
            </div>
            <div className="setting-item">
              <label>{t("settings.authPasswordConfirm")}</label>
              <input
                type="password"
                value={passwordConfirm}
                onChange={(e) => setPasswordConfirm(e.target.value)}
                disabled={settings.AUTH_ENABLED !== "true"}
              />
            </div>
            <div className="setting-item setting-item-block">
              <label>{t("settings.authBypassCidr")}</label>
              <textarea
                value={settings.AUTH_BYPASS_CIDR}
                placeholder={"e.g. 127.0.0.1/32, ::1/128"}
                onChange={(e) => {
                  updateSetting("AUTH_BYPASS_CIDR", e.target.value);
                  const err = validateCidrList(e.target.value);
                  setCidrError(err);
                }}
                disabled={settings.AUTH_ENABLED !== "true"}
                rows={2}
              />
              {cidrError && (
                <div className="setting-error">
                  {cidrError}
                </div>
              )}
              <p className="setting-description">
                {t("settings.authBypassCidrDescription")}
              </p>
            </div>
          </div>
          )}

          <div className="settings-section">
            <h4>{t("settings.offGridWarningSection")}</h4>
            <div className="setting-item">
              <label>
                <input
                  type="checkbox"
                  checked={settings.OFF_GRID_WARNING_ENABLED === "true"}
                  onChange={(e) =>
                    updateSetting(
                      "OFF_GRID_WARNING_ENABLED",
                      e.target.checked ? "true" : "false"
                    )
                  }
                />
                {t("settings.offGridWarningEnabled")}
              </label>
            </div>
            <div className="setting-item">
              <label>{t("settings.offGridWarningPower")}</label>
              <input
                type="number"
                value={settings.OFF_GRID_WARNING_POWER}
                onChange={(e) =>
                  updateSetting("OFF_GRID_WARNING_POWER", e.target.value)
                }
                disabled={settings.OFF_GRID_WARNING_ENABLED !== "true"}
              />
              <span>{t("settings.watts")}</span>
            </div>
            <div className="setting-item">
              <label>{t("settings.offGridWarningSoc")}</label>
              <input
                type="number"
                value={settings.OFF_GRID_WARNING_SOC}
                onChange={(e) =>
                  updateSetting("OFF_GRID_WARNING_SOC", e.target.value)
                }
                disabled={settings.OFF_GRID_WARNING_ENABLED !== "true"}
              />
              <span>{t("settings.percent")}</span>
            </div>
            <div className="setting-item">
              <label>{t("settings.maxBatteryPower")}</label>
              <input
                type="number"
                value={settings.MAX_BATTERY_POWER}
                onChange={(e) =>
                  updateSetting("MAX_BATTERY_POWER", e.target.value)
                }
                disabled={settings.OFF_GRID_WARNING_ENABLED !== "true"}
              />
              <span>{t("settings.watts")}</span>
            </div>
            <p className="setting-description">
              {t("settings.offGridWarningDescription")}
            </p>
          </div>
        </div>
        <div className="settings-actions">
          <button onClick={handleSave} disabled={
            saving || !hasChanges() || cidrError !== null || (settings.AUTH_ENABLED === 'true' && (
              !settings.AUTH_USERNAME || settings.AUTH_USERNAME.trim() === '' ||
              !settings.AUTH_PASSWORD || settings.AUTH_PASSWORD === '' ||
              settings.AUTH_PASSWORD !== passwordConfirm
            ))
          }>
            {saving ? t("settings.saving") : t("settings.save")}
          </button>
        </div>
      </div>
    </div>
  );
});

export default SettingsPopover;
