export function money(value: number | null | undefined): string {
  return `$${Number(value ?? 0).toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
}

export function optionalMoney(value: number | null | undefined): string {
  return value === null || value === undefined ? "-" : money(value);
}

export function pct(value: unknown): string {
  const number = typeof value === "number" ? value : Number(value ?? 0);
  return `${(number * 100).toFixed(2)}%`;
}

export function optionalPct(value: unknown): string {
  if (value === null || value === undefined || value === "") {
    return "-";
  }
  const number = typeof value === "number" ? value : Number(value);
  return Number.isFinite(number) ? `${(number * 100).toFixed(2)}%` : "-";
}

export function optionalDecimal(value: unknown, digits = 3): string {
  if (value === null || value === undefined || value === "") {
    return "-";
  }
  const number = typeof value === "number" ? value : Number(value);
  return Number.isFinite(number) ? number.toFixed(digits) : "-";
}

export function numberValue(value: unknown): number {
  return typeof value === "number" ? value : Number(value ?? 0);
}

export function numberMaybe(value: unknown): number | null {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

export function textValue(value: unknown, fallback = "-"): string {
  if (value === null || value === undefined || value === "") {
    return fallback;
  }
  return String(value);
}

export function nullableText(value: unknown): string | null {
  if (value === null || value === undefined || value === "") {
    return null;
  }
  return String(value);
}

export function booleanText(value: unknown): string {
  return value === true ? "是" : value === false ? "否" : "-";
}
