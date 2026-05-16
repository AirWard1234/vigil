"use client";

import { useMemo, useState } from "react";
import type { DailyVerdict, Verdict } from "../lib/api";
import {
  colors,
  fmtSigned,
  mono,
  numberStyle,
  panelLabelStyle,
  verdictColor,
} from "../lib/theme";

type Filter = "ALL" | Verdict;

const filterButtons: { label: Filter; color: string }[] = [
  { label: "ALL", color: colors.text },
  { label: "GREEN", color: colors.green },
  { label: "YELLOW", color: colors.yellow },
  { label: "RED", color: colors.red },
];

const th = {
  textAlign: "left" as const,
  padding: "10px 12px",
  borderBottom: `1px solid ${colors.borderStrong}`,
  color: colors.textMuted,
  fontSize: 11,
  letterSpacing: 1.5,
  textTransform: "uppercase" as const,
  fontWeight: 400,
  whiteSpace: "nowrap" as const,
};

const td = {
  padding: "10px 12px",
  borderBottom: `1px solid ${colors.border}`,
  fontFamily: mono,
  fontSize: 13,
  whiteSpace: "nowrap" as const,
};

export default function HistoryTable({
  verdicts,
}: {
  verdicts: DailyVerdict[];
}) {
  const [filter, setFilter] = useState<Filter>("ALL");

  const rows = useMemo(() => {
    if (filter === "ALL") return verdicts;
    return verdicts.filter((v) => v.verdict === filter);
  }, [verdicts, filter]);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          flexWrap: "wrap",
          gap: 12,
        }}
      >
        <span style={panelLabelStyle}>History · last {verdicts.length} days</span>
        <div style={{ display: "flex", gap: 6 }}>
          {filterButtons.map((b) => {
            const active = b.label === filter;
            return (
              <button
                key={b.label}
                onClick={() => setFilter(b.label)}
                style={{
                  fontFamily: mono,
                  fontSize: 11,
                  letterSpacing: 2,
                  padding: "6px 12px",
                  background: active ? `${b.color}22` : "transparent",
                  color: active ? b.color : colors.textMuted,
                  border: `1px solid ${active ? b.color : colors.border}`,
                  borderRadius: 2,
                  cursor: "pointer",
                  textTransform: "uppercase",
                }}
              >
                {b.label}
              </button>
            );
          })}
        </div>
      </div>

      <div
        style={{
          background: colors.card,
          border: `1px solid ${colors.border}`,
          borderRadius: 2,
          overflowX: "auto",
        }}
      >
        <table
          style={{
            width: "100%",
            borderCollapse: "collapse",
            minWidth: 980,
          }}
        >
          <thead>
            <tr>
              <th style={th}>Date</th>
              <th style={th}>Verdict</th>
              <th style={th}>Regime</th>
              <th style={{ ...th, textAlign: "right" }}>Strikes</th>
              <th style={{ ...th, textAlign: "right" }}>Semi Health</th>
              <th style={th}>GEX</th>
              <th style={{ ...th, textAlign: "right" }}>Semi Sent.</th>
              <th style={{ ...th, textAlign: "right" }}>Macro Sent.</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={8} style={{ ...td, color: colors.textMuted }}>
                  No matching verdicts.
                </td>
              </tr>
            ) : (
              rows.map((v) => <HistoryRow key={v.date} v={v} />)
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function HistoryRow({ v }: { v: DailyVerdict }) {
  const vColor = verdictColor(v.verdict);
  const rowBg = v.verdict === "RED"
    ? `${colors.red}0d`
    : v.verdict === "YELLOW"
      ? `${colors.yellow}0d`
      : v.verdict === "GREEN"
        ? `${colors.green}08`
        : "transparent";

  const semiScore = v.semi_health_score;
  const semiColor =
    semiScore == null
      ? colors.text
      : semiScore >= 70
        ? colors.green
        : semiScore >= 40
          ? colors.yellow
          : colors.red;

  const gexLabel = (v.gex_label ?? "").toLowerCase();
  const gexColor = gexLabel.includes("negative")
    ? colors.red
    : gexLabel.includes("positive")
      ? colors.green
      : colors.text;

  return (
    <tr style={{ background: rowBg }}>
      <td style={td}>{v.date}</td>
      <td style={td}>
        <span
          style={{
            ...numberStyle,
            padding: "3px 10px",
            border: `1px solid ${vColor}`,
            color: vColor,
            fontSize: 11,
            letterSpacing: 2,
          }}
        >
          {v.verdict ?? "—"}
        </span>
      </td>
      <td style={{ ...td, color: colors.text }}>
        {v.regime_label ?? "—"}
        {v.regime_confidence != null && (
          <span style={{ color: colors.textDim }}>
            {" "}
            ({Math.round(
              (v.regime_confidence <= 1
                ? v.regime_confidence * 100
                : v.regime_confidence),
            )}
            %)
          </span>
        )}
      </td>
      <td style={{ ...td, textAlign: "right", color: vColor }}>
        {v.strike_count ?? "—"}
      </td>
      <td style={{ ...td, textAlign: "right", color: semiColor }}>
        {semiScore != null ? semiScore.toFixed(0) : "—"}
      </td>
      <td style={{ ...td, color: gexColor }}>{v.gex_label ?? "—"}</td>
      <td
        style={{
          ...td,
          textAlign: "right",
          color: sentColor(v.semi_sentiment_score),
        }}
      >
        {fmtSigned(v.semi_sentiment_score, 2)}
      </td>
      <td
        style={{
          ...td,
          textAlign: "right",
          color: sentColor(v.macro_sentiment_score),
        }}
      >
        {fmtSigned(v.macro_sentiment_score, 2)}
      </td>
    </tr>
  );
}

function sentColor(s: number | null | undefined): string {
  if (s == null) return colors.text;
  if (s > 0.1) return colors.green;
  if (s < -0.1) return colors.red;
  return colors.yellow;
}
