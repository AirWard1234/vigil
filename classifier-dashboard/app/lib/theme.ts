import type { CSSProperties } from "react";
import type { Verdict } from "./api";

export const colors = {
  bg: "#0a0a0a",
  card: "#111111",
  cardAlt: "#161616",
  border: "#222222",
  borderStrong: "#333333",
  text: "#e6e6e6",
  textMuted: "#888888",
  textDim: "#5a5a5a",
  green: "#00ff7f",
  yellow: "#ffd700",
  red: "#ff4444",
  accent: "#66ccff",
} as const;

export const mono =
  "'JetBrains Mono', 'SF Mono', 'Menlo', 'Monaco', 'Consolas', monospace";

export function verdictColor(v: Verdict | string | null | undefined): string {
  switch (v) {
    case "GREEN":
      return colors.green;
    case "YELLOW":
      return colors.yellow;
    case "RED":
      return colors.red;
    default:
      return colors.textMuted;
  }
}

export const panelStyle: CSSProperties = {
  background: colors.card,
  border: `1px solid ${colors.border}`,
  padding: "16px 18px",
  borderRadius: 2,
};

export const panelLabelStyle: CSSProperties = {
  color: colors.textMuted,
  fontSize: 11,
  letterSpacing: 1.5,
  textTransform: "uppercase",
  marginBottom: 10,
};

export const numberStyle: CSSProperties = {
  fontFamily: mono,
  fontVariantNumeric: "tabular-nums",
};

export function fmtNum(
  v: number | null | undefined,
  digits = 2,
  suffix = "",
): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return `${v.toFixed(digits)}${suffix}`;
}

export function fmtSigned(
  v: number | string | null | undefined,
  digits = 2,
  suffix = "",
): string {
  if (v === null || v === undefined || isNaN(Number(v))) return "—";
  const n = Number(v);
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(digits)}${suffix}`;
}

export function fmtPct(v: number | null | undefined, digits = 0): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const pct = v <= 1 && v >= -1 ? v * 100 : v;
  return `${pct.toFixed(digits)}%`;
}
