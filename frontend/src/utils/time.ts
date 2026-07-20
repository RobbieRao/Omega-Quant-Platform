/**
 * Format helpers for US market times. All UI defaults to America/New_York since
 * Omega only trades US equities + options.
 */

const US_FORMATTER = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "America/New_York",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

const US_DATE_FORMATTER = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "America/New_York",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

const US_TIME_FORMATTER = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "America/New_York",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

export function formatUsDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const d = new Date(value);
  if (!Number.isFinite(d.getTime())) return "—";
  return `${US_FORMATTER.format(d)} ET`;
}

export function formatUsDate(value: string | null | undefined): string {
  if (!value) return "—";
  const d = new Date(value);
  if (!Number.isFinite(d.getTime())) return "—";
  return US_DATE_FORMATTER.format(d);
}

export function formatUsTime(value: string | null | undefined): string {
  if (!value) return "—";
  const d = new Date(value);
  if (!Number.isFinite(d.getTime())) return "—";
  return US_TIME_FORMATTER.format(d);
}
