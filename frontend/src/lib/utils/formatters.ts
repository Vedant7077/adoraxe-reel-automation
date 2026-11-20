/**
 * Date and Time formatters for Adoraxe.
 * Safely parses UTC timestamps from API/database and formats
 * into the user's browser-local timezone (e.g. Asia/Kolkata / IST).
 */

export function parseApiDate(dateStr: string | null | undefined): Date | null {
  if (!dateStr) return null;
  let s = String(dateStr).trim();
  if (!s) return null;
  // If the string lacks a timezone offset (+/-HH:MM or Z), treat as UTC
  if (!s.endsWith('Z') && !/[+-]\d{2}:?\d{2}$/.test(s)) {
    s += 'Z';
  }
  const d = new Date(s);
  return isNaN(d.getTime()) ? null : d;
}

export function formatLocalDateTime(dateStr: string | null | undefined): string {
  const d = parseApiDate(dateStr);
  if (!d) return '—';
  return new Intl.DateTimeFormat(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
    hour12: true,
  }).format(d);
}

export function formatLocalDate(dateStr: string | null | undefined): string {
  const d = parseApiDate(dateStr);
  if (!d) return '—';
  return new Intl.DateTimeFormat(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  }).format(d);
}

export function formatLocalTime(dateStr: string | null | undefined): string {
  const d = parseApiDate(dateStr);
  if (!d) return '—';
  return new Intl.DateTimeFormat(undefined, {
    hour: 'numeric',
    minute: '2-digit',
    hour12: true,
  }).format(d);
}
