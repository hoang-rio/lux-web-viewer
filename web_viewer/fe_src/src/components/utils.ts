import type { PointAnnotations } from "apexcharts";
import type { INotificationData } from "../Intefaces";

export function fixedIfNeed(num: number, fixedNumber = 1) {
  if (num > 0) {
    return num.toFixed(fixedNumber);
  }
  return num;
}

export const MWH_UNIT_CLASS = "unit-mwh";

export interface IFormattedTotal {
  value: number | string;
  unit: string;
  unitClassName?: string;
}

export function formatTotalValue(kwh: number): IFormattedTotal {
  if (kwh > 1000) {
    return {
      value: fixedIfNeed(kwh / 1000),
      unit: " MWh",
      unitClassName: MWH_UNIT_CLASS,
    };
  }
  return { value: fixedIfNeed(kwh), unit: " kWh" };
}

export const roundTo = (num: number, fixedNumber = 1): number => {
  const numToFixed = Math.pow(10, fixedNumber);
  return Math.round(num * numToFixed) / numToFixed;
};

export const NOTIFICATION_MARKER_COLOR = "#f59e0b";

/**
 * Escapes text for safe interpolation into markup. ApexCharts assigns
 * annotation tooltip output via innerHTML, so every untrusted field
 * (notification title/body) must pass through this first.
 */
export function escapeHtml(value: unknown): string {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/**
 * Parses a server timestamp to epoch milliseconds. Server timestamps are
 * server-local wall clock in "YYYY-MM-DD HH:mm:ss", a space-separated form
 * that only ECMAScript-implementation-dependent Date parsing accepts, so it
 * is normalized to the spec-guaranteed "T" form first. Values written before
 * the format was normalized (ISO-8601 with microseconds) are also accepted.
 * Returns NaN when the value cannot be parsed.
 */
export function parseServerTimestamp(value: string | number): number {
  if (typeof value === "number") {
    return Number.isFinite(value) ? value : NaN;
  }
  const raw = String(value ?? "").trim();
  if (!raw) {
    return NaN;
  }
  // Space-separated form -> ISO-like form, and drop sub-millisecond digits
  // that Date parsing is not guaranteed to accept.
  const normalized = raw.replace(" ", "T").replace(/(\.\d{3})\d+$/, "$1");
  const parsed = new Date(normalized).getTime();
  return Number.isFinite(parsed) ? parsed : NaN;
}

function formatClock(timestamp: number): string {
  const value = new Date(timestamp);
  const hours = String(value.getHours()).padStart(2, "0");
  const minutes = String(value.getMinutes()).padStart(2, "0");
  return `${hours}:${minutes}`;
}

/**
 * Builds ApexCharts point annotations for notifications, pinned to the top of
 * the plot (no `y`) and carrying the notification content in a native hover
 * tooltip. Notifications falling in the same minute are merged into one
 * marker so they do not overlap.
 */
export function buildNotificationAnnotations(
  notifications: INotificationData[],
  notifiedAtLabel: string
): PointAnnotations[] {
  const byMinute = new Map<number, INotificationData[]>();
  notifications.forEach((notification) => {
    const timestamp = parseServerTimestamp(notification.notified_at);
    if (!Number.isFinite(timestamp)) {
      return;
    }
    const key = Math.floor(timestamp / 60000);
    const bucket = byMinute.get(key);
    if (bucket) {
      bucket.push(notification);
    } else {
      byMinute.set(key, [notification]);
    }
  });

  return Array.from(byMinute.keys())
    .sort((a, b) => a - b)
    .map((minuteKey) => {
      const x = minuteKey * 60000;
      const group = byMinute.get(minuteKey) ?? [];
      const details = group
        .map((notification) => {
          const title = String(notification.title ?? "").trim();
          const body = String(notification.body ?? "").trim();
          const summary = [title, body].filter(Boolean).join(": ");
          return summary ? `<div>${escapeHtml(summary)}</div>` : "";
        })
        .join("");
      return {
        x,
        marker: {
          size: 8,
          shape: "circle",
          fillColor: NOTIFICATION_MARKER_COLOR,
          strokeColor: NOTIFICATION_MARKER_COLOR,
          strokeWidth: 0,
        },
        tooltip: {
          enabled: true,
          formatter: () =>
            `<div><strong>${escapeHtml(
              notifiedAtLabel
            )} ${formatClock(x)}</strong></div>${details}`,
        },
      };
    });
}