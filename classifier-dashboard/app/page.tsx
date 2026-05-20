import {
  fetchAccuracy,
  fetchLatest,
  type AccuracyStats,
  type DailyVerdict,
  type Headline,
} from "./lib/api";
import {
  colors,
  fmtNum,
  fmtPct,
  fmtSigned,
  mono,
  numberStyle,
  panelLabelStyle,
  panelStyle,
  verdictColor,
} from "./lib/theme";

export const dynamic = "force-dynamic";

export default async function TodayPage() {
  let data: DailyVerdict | null = null;
  let error: string | null = null;
  let accuracy: AccuracyStats | null = null;
  try {
    const [latest, acc] = await Promise.allSettled([
      fetchLatest(),
      fetchAccuracy(30),
    ]);
    if (latest.status === "fulfilled") {
      data = latest.value;
    } else {
      error =
        latest.reason instanceof Error
          ? latest.reason.message
          : String(latest.reason);
    }
    if (acc.status === "fulfilled") {
      accuracy = acc.value;
    }
  } catch (e) {
    error = e instanceof Error ? e.message : String(e);
  }

  if (error || !data) {
    return (
      <div style={{ ...panelStyle, color: colors.red }}>
        <div style={panelLabelStyle}>Pipeline offline</div>
        <div style={{ fontFamily: mono, fontSize: 13 }}>
          {error ?? "No verdict available."}
        </div>
      </div>
    );
  }

  const vColor = verdictColor(data.verdict);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      <RegimeHeader data={data} />
      <VerdictCard data={data} verdictColor={vColor} />
      <PanelGrid data={data} />
      <ExpectedRange data={data} />
      {accuracy && <AccuracyPanel stats={accuracy} />}
      <Headlines data={data} />
      <EventRisk data={data} />
      <ReasonBlock data={data} />
    </div>
  );
}

function AccuracyPanel({ stats }: { stats: AccuracyStats }) {
  const building = stats.reconciled_days < 5;
  const fmt = (v: number | null) =>
    v === null || v === undefined ? "—" : `${v.toFixed(1)}%`;

  const cell = (label: string, value: string, color?: string) => (
    <div
      key={label}
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 4,
        padding: "10px 14px",
        border: `1px solid ${colors.border}`,
        borderRadius: 2,
        background: colors.cardAlt,
        minWidth: 120,
      }}
    >
      <span style={{ ...panelLabelStyle, marginBottom: 0 }}>{label}</span>
      <span
        style={{
          ...numberStyle,
          fontSize: 20,
          color: color ?? colors.text,
        }}
      >
        {value}
      </span>
    </div>
  );

  return (
    <div style={panelStyle}>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "baseline",
          marginBottom: 10,
        }}
      >
        <span style={panelLabelStyle}>Accuracy · last 30 days</span>
        {building && (
          <span
            style={{
              ...numberStyle,
              fontSize: 11,
              color: colors.yellow,
              letterSpacing: 1.5,
              textTransform: "uppercase",
            }}
          >
            Building data…
          </span>
        )}
      </div>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))",
          gap: 10,
        }}
      >
        {cell(
          "Verdict",
          building ? "—" : fmt(stats.verdict_accuracy),
          building ? colors.textDim : colors.accent,
        )}
        {cell(
          "Range (1σ)",
          building ? "—" : fmt(stats.range_accuracy_1sigma),
          building ? colors.textDim : colors.text,
        )}
        {cell(
          "Regime",
          building ? "—" : fmt(stats.regime_accuracy),
          building ? colors.textDim : colors.text,
        )}
        {cell(
          "Days tracked",
          `${stats.reconciled_days}/${stats.total_days}`,
          colors.textMuted,
        )}
      </div>
    </div>
  );
}

function RegimeHeader({ data }: { data: DailyVerdict }) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "baseline",
        justifyContent: "space-between",
        flexWrap: "wrap",
        gap: 12,
      }}
    >
      <div style={{ display: "flex", alignItems: "baseline", gap: 16 }}>
        <span
          style={{
            ...numberStyle,
            fontSize: 26,
            color: colors.text,
            letterSpacing: 2,
            textTransform: "uppercase",
          }}
        >
          {data.regime_label ?? "—"}
        </span>
        <span
          style={{
            ...numberStyle,
            fontSize: 16,
            color: colors.accent,
          }}
        >
          {fmtPct(data.regime_confidence, 0)}
        </span>
        <span
          style={{
            color: colors.textDim,
            fontSize: 11,
            letterSpacing: 2,
            textTransform: "uppercase",
          }}
        >
          confidence
        </span>
      </div>
      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          gap: 10,
          color: colors.textMuted,
          fontSize: 12,
        }}
      >
        <span style={{ ...numberStyle, color: colors.text }}>{data.date}</span>
        {data.stale && (
          <span
            style={{
              padding: "2px 6px",
              border: `1px solid ${colors.yellow}`,
              color: colors.yellow,
              fontSize: 10,
              letterSpacing: 1.5,
              textTransform: "uppercase",
            }}
          >
            Stale
          </span>
        )}
      </div>
    </div>
  );
}

function VerdictCard({
  data,
  verdictColor: vColor,
}: {
  data: DailyVerdict;
  verdictColor: string;
}) {
  return (
    <div
      style={{
        background: colors.card,
        border: `2px solid ${vColor}`,
        borderRadius: 2,
        padding: "32px 28px",
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: 24,
        flexWrap: "wrap",
      }}
    >
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <span style={panelLabelStyle}>Verdict</span>
        <span
          style={{
            ...numberStyle,
            fontSize: 64,
            lineHeight: 1,
            color: vColor,
            letterSpacing: 6,
            fontWeight: 700,
          }}
        >
          {data.verdict ?? "—"}
        </span>
      </div>
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "flex-end",
          gap: 6,
        }}
      >
        <span style={panelLabelStyle}>Strikes</span>
        <span
          style={{
            ...numberStyle,
            fontSize: 48,
            color: vColor,
            lineHeight: 1,
          }}
        >
          {data.strike_count ?? "—"}
        </span>
        {data.strikes_triggered && data.strikes_triggered.length > 0 && (
          <div
            style={{
              marginTop: 8,
              display: "flex",
              flexWrap: "wrap",
              gap: 6,
              justifyContent: "flex-end",
              maxWidth: 520,
            }}
          >
            {data.strikes_triggered.map((s) => (
              <span
                key={s}
                style={{
                  ...numberStyle,
                  fontSize: 11,
                  padding: "3px 8px",
                  border: `1px solid ${colors.borderStrong}`,
                  color: colors.textMuted,
                  textTransform: "uppercase",
                  letterSpacing: 1,
                }}
              >
                {s}
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function PanelGrid({ data }: { data: DailyVerdict }) {
  // Fixed 4 columns so the 8 panels fill two even rows — `auto-fit` packed
  // 5 per row and left a ragged gap of empty cells on wide screens.
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "repeat(4, minmax(0, 1fr))",
        gap: 12,
      }}
    >
      <YieldPanel data={data} />
      <SemiPanel data={data} />
      <VixPanel data={data} />
      <IvPanel data={data} />
      <GexPanel data={data} />
      <BiasPanel data={data} />
      <OpenBiasPanel data={data} />
      <OvernightPanel data={data} />
    </div>
  );
}

const GAP_COLORS: Record<string, string> = {
  "Gap Up": "#00ff88",
  "Gap Down": "#ff4444",
  "Flat Open": "#ffffff",
};

const HOLD_COLORS: Record<string, string> = {
  "Open likely holds": "#00ff88",
  "Open direction uncertain": "#ffaa00",
  "Open likely fades": "#ff4444",
};

function OpenBiasPanel({ data }: { data: DailyVerdict }) {
  const gapLabel = data.gap_label ?? "—";
  const gapPct = data.gap_pct;
  const openHold = data.open_hold ?? "—";
  const sweepRisk = data.sweep_risk;
  const gapColor = GAP_COLORS[gapLabel] ?? colors.text;
  const holdColor = HOLD_COLORS[openHold] ?? colors.text;

  return (
    <div
      style={{
        ...panelStyle,
        display: "flex",
        flexDirection: "column",
        gap: 10,
      }}
    >
      <div style={panelLabelStyle}>Open Bias</div>

      <div
        style={{
          ...numberStyle,
          fontSize: 22,
          textAlign: "center",
          color: gapColor,
          letterSpacing: 3,
          textTransform: "uppercase",
          fontWeight: 700,
          marginTop: 4,
        }}
      >
        {gapLabel}
      </div>

      <div
        style={{
          ...numberStyle,
          fontSize: 16,
          textAlign: "center",
          color: gapColor,
        }}
      >
        {gapPct != null
          ? `${gapPct >= 0 ? "+" : ""}${gapPct.toFixed(2)}%`
          : "—"}
      </div>

      <div
        style={{
          ...numberStyle,
          fontSize: 12,
          letterSpacing: 1.5,
          textTransform: "uppercase",
          color: holdColor,
          textAlign: "center",
          marginTop: 4,
          fontWeight: 700,
        }}
      >
        {openHold}
      </div>

      {sweepRisk && (
        <div
          style={{
            fontFamily: mono,
            fontSize: 11,
            color: colors.red,
            lineHeight: 1.4,
            textAlign: "center",
            border: `1px solid ${colors.red}66`,
            padding: "6px 8px",
            borderRadius: 2,
          }}
        >
          {sweepRisk}
        </div>
      )}

      <div
        style={{
          fontFamily: mono,
          fontSize: 9,
          color: colors.textDim,
          fontStyle: "italic",
          textAlign: "center",
          marginTop: "auto",
        }}
      >
        Pre-market estimate only — conditions change at open
      </div>
    </div>
  );
}

const BIAS_COLORS: Record<string, string> = {
  Bullish: "#00ff88",
  "Lean Bullish": "#88ffbb",
  Neutral: "#ffffff",
  "Lean Bearish": "#ffaa00",
  Bearish: "#ff4444",
  "No Bias": "#666666",
};

function BiasPanel({ data }: { data: DailyVerdict }) {
  const label = data.bias_label ?? "Neutral";
  const score = data.bias_score ?? 0;
  const conviction = data.bias_conviction ?? "Low";
  const reason = data.bias_reason ?? "";
  const labelColor = BIAS_COLORS[label] ?? colors.text;

  // Center marker at 0, fill segment from center to score position.
  // -100 maps to 0%, 0 to 50%, +100 to 100%.
  const clamped = Math.max(-100, Math.min(100, score));
  const fillLeftPct = clamped < 0 ? 50 + clamped / 2 : 50;
  const fillWidthPct = Math.abs(clamped) / 2;

  const convictionLabel =
    conviction === "High"
      ? "HIGH CONVICTION"
      : conviction === "Moderate"
        ? "MODERATE"
        : conviction === "Low"
          ? "LOW"
          : "NONE";

  return (
    <div
      style={{
        ...panelStyle,
        display: "flex",
        flexDirection: "column",
        gap: 10,
      }}
    >
      <div style={panelLabelStyle}>Directional Bias</div>

      <div
        style={{
          ...numberStyle,
          fontSize: 22,
          textAlign: "center",
          color: labelColor,
          letterSpacing: 3,
          textTransform: "uppercase",
          fontWeight: 700,
          marginTop: 4,
        }}
      >
        {label}
      </div>

      <div style={{ marginTop: 6 }}>
        <div
          style={{
            position: "relative",
            height: 10,
            background: colors.cardAlt,
            border: `1px solid ${colors.border}`,
            borderRadius: 2,
          }}
        >
          <div
            style={{
              position: "absolute",
              left: `${fillLeftPct}%`,
              width: `${fillWidthPct}%`,
              top: 0,
              bottom: 0,
              background: labelColor,
              borderRadius: 2,
            }}
          />
          <div
            style={{
              position: "absolute",
              left: "50%",
              top: -2,
              bottom: -2,
              width: 1,
              background: colors.textMuted,
            }}
          />
        </div>
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            ...numberStyle,
            fontSize: 10,
            color: colors.textDim,
            marginTop: 4,
          }}
        >
          <span>-100</span>
          <span
            style={{
              ...numberStyle,
              fontSize: 13,
              color: labelColor,
              fontWeight: 700,
            }}
          >
            {score >= 0 ? `+${score.toFixed(1)}` : score.toFixed(1)}
          </span>
          <span>+100</span>
        </div>
      </div>

      <div
        style={{
          ...numberStyle,
          fontSize: 10,
          letterSpacing: 1.5,
          textTransform: "uppercase",
          color: labelColor,
          textAlign: "center",
          marginTop: 2,
        }}
      >
        {convictionLabel}
      </div>

      {reason && (
        <div
          style={{
            fontFamily: mono,
            fontSize: 11,
            color: colors.textMuted,
            lineHeight: 1.4,
            display: "-webkit-box",
            WebkitLineClamp: 2,
            WebkitBoxOrient: "vertical",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}
        >
          {reason}
        </div>
      )}

      <div
        style={{
          fontFamily: mono,
          fontSize: 9,
          color: colors.textDim,
          fontStyle: "italic",
          textAlign: "center",
          marginTop: "auto",
        }}
      >
        Directional lean only — not a trade signal
      </div>
    </div>
  );
}

function Row({
  label,
  value,
  color,
}: {
  label: string;
  value: string;
  color?: string;
}) {
  return (
    <div
      style={{
        display: "flex",
        justifyContent: "space-between",
        alignItems: "baseline",
        padding: "4px 0",
        borderBottom: `1px dashed ${colors.border}`,
      }}
    >
      <span style={{ color: colors.textMuted, fontSize: 12 }}>{label}</span>
      <span
        style={{
          ...numberStyle,
          fontSize: 13,
          color: color ?? colors.text,
        }}
      >
        {value}
      </span>
    </div>
  );
}

function YieldPanel({ data }: { data: DailyVerdict }) {
  const yc =
    data.yield_bps_change == null
      ? colors.text
      : data.yield_bps_change > 0
        ? colors.red
        : data.yield_bps_change < 0
          ? colors.green
          : colors.text;
  const dxyLabel = data.dxy_label ?? "Unavailable";
  const dxyColor =
    dxyLabel === "Dollar Strength — headwind"
      ? colors.red
      : dxyLabel === "Dollar Weakness — tailwind"
        ? colors.green
        : colors.text;
  return (
    <div style={panelStyle}>
      <div style={panelLabelStyle}>Yield</div>
      <Row
        label="10Y change"
        value={fmtSigned(data.yield_bps_change, 1, " bps")}
        color={yc}
      />
      <Row
        label="ROC"
        value={data.yield_accelerating ? "Accelerating" : "Stable"}
        color={data.yield_accelerating ? colors.yellow : colors.text}
      />
      <Row
        label="DXY"
        value={
          data.dxy_change != null
            ? `${fmtSigned(data.dxy_change, 1, "%")}  ${dxyLabel}`
            : "Unavailable"
        }
        color={data.dxy_change != null ? dxyColor : colors.textMuted}
      />
    </div>
  );
}

function SemiPanel({ data }: { data: DailyVerdict }) {
  const score = data.semi_health_score;
  const sc =
    score == null
      ? colors.text
      : score >= 70
        ? colors.green
        : score >= 40
          ? colors.yellow
          : colors.red;
  return (
    <div style={panelStyle}>
      <div style={panelLabelStyle}>Semiconductors</div>
      <Row
        label="Health"
        value={
          score != null
            ? `${score.toFixed(0)}/100${data.semi_health_label ? " · " + data.semi_health_label : ""}`
            : "—"
        }
        color={sc}
      />
      <Row label="SMH/SPY" value={fmtSigned(data.smh_vs_spy, 2, "%")} />
      <Row label="SMH/QQQ" value={fmtSigned(data.smh_vs_qqq, 2, "%")} />
    </div>
  );
}

function VixPanel({ data }: { data: DailyVerdict }) {
  const vts = data.vix_term_structure ?? "—";
  const vtsColor =
    vts.toLowerCase().includes("backward")
      ? colors.red
      : vts.toLowerCase().includes("contango")
        ? colors.green
        : colors.text;
  const spreadLabel = data.vix_spread_label ?? "Neutral";
  const spreadColor =
    spreadLabel === "Elevated Near-Term Fear"
      ? colors.red
      : spreadLabel === "Near-Term Calm"
        ? colors.green
        : colors.text;
  return (
    <div style={panelStyle}>
      <div style={panelLabelStyle}>VIX Complex</div>
      <Row label="Term Structure" value={vts} color={vtsColor} />
      <Row
        label="VIX Spread"
        value={
          data.vix_spread != null
            ? `${fmtSigned(data.vix_spread, 1)}  ${spreadLabel}`
            : "—"
        }
        color={data.vix_spread != null ? spreadColor : colors.text}
      />
      <Row
        label="RV / IV"
        value={fmtNum(data.realized_vs_implied, 2, "×")}
      />
    </div>
  );
}

function IvPanel({ data }: { data: DailyVerdict }) {
  return (
    <div style={panelStyle}>
      <div style={panelLabelStyle}>Options IV</div>
      <Row
        label="SMH IV"
        value={fmtPct(data.smh_iv, 1)}
        color={data.smh_iv_elevated ? colors.yellow : colors.text}
      />
      <Row
        label="NVDA IV"
        value={fmtPct(data.nvda_iv, 1)}
        color={data.nvda_iv_elevated ? colors.yellow : colors.text}
      />
    </div>
  );
}

function GexPanel({ data }: { data: DailyVerdict }) {
  const label = (data.gex_label ?? "").toLowerCase();
  const gexColor = label.includes("negative")
    ? colors.red
    : label.includes("positive")
      ? colors.green
      : colors.text;
  return (
    <div style={panelStyle}>
      <div style={panelLabelStyle}>GEX</div>
      <Row
        label="State"
        value={data.gex_label ?? "—"}
        color={gexColor}
      />
      <Row label="Value" value={fmtNum(data.gex_value, 2)} />
      <Row
        label="MNQ Level"
        value={
          data.gex_key_level_mnq != null
            ? data.gex_key_level_mnq.toLocaleString(undefined, {
                maximumFractionDigits: 0,
              })
            : "—"
        }
        color={colors.accent}
      />
    </div>
  );
}

function OvernightPanel({ data }: { data: DailyVerdict }) {
  const semi = data.semi_sentiment_score;
  const macro = data.macro_sentiment_score;
  const sentColor = (s: number | null) =>
    s == null
      ? colors.text
      : s > 0.1
        ? colors.green
        : s < -0.1
          ? colors.red
          : colors.yellow;
  return (
    <div style={panelStyle}>
      <div style={panelLabelStyle}>Overnight Intelligence</div>
      <Row
        label="Semi sentiment"
        value={fmtSigned(semi, 2)}
        color={sentColor(semi)}
      />
      <Row
        label="Macro sentiment"
        value={fmtSigned(macro, 2)}
        color={sentColor(macro)}
      />
      <Row
        label="Guidance cut"
        value={data.guidance_cut_flag ? "Flagged" : "None"}
        color={data.guidance_cut_flag ? colors.red : colors.text}
      />
    </div>
  );
}

function ExpectedRange({ data }: { data: DailyVerdict }) {
  const oneLow = data.one_sigma_low;
  const oneHigh = data.one_sigma_high;
  const twoLow = data.expected_range_low;
  const twoHigh = data.expected_range_high;
  const gex = data.gex_key_level_mnq;

  if (oneLow == null && twoLow == null) {
    return null;
  }

  const lo = Math.min(
    ...[oneLow, twoLow, gex].filter((v): v is number => v != null),
  );
  const hi = Math.max(
    ...[oneHigh, twoHigh, gex].filter((v): v is number => v != null),
  );
  const span = Math.max(hi - lo, 1e-9);
  const pad = span * 0.08;
  const scaleLo = lo - pad;
  const scaleHi = hi + pad;
  const scaleSpan = scaleHi - scaleLo;

  const pos = (v: number) => ((v - scaleLo) / scaleSpan) * 100;

  return (
    <div style={panelStyle}>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "baseline",
        }}
      >
        <span style={panelLabelStyle}>Expected Range</span>
        <span
          style={{
            ...numberStyle,
            fontSize: 13,
            color: colors.text,
          }}
        >
          {twoLow != null && twoHigh != null
            ? `${twoLow.toFixed(0)} — ${twoHigh.toFixed(0)}`
            : oneLow != null && oneHigh != null
              ? `${oneLow.toFixed(0)} — ${oneHigh.toFixed(0)}`
              : "—"}
        </span>
      </div>

      <div
        style={{
          position: "relative",
          height: 50,
          marginTop: 22,
          marginBottom: 8,
        }}
      >
        <div
          style={{
            position: "absolute",
            top: 22,
            left: 0,
            right: 0,
            height: 4,
            background: colors.borderStrong,
            borderRadius: 2,
          }}
        />
        {twoLow != null && twoHigh != null && (
          <div
            style={{
              position: "absolute",
              top: 20,
              left: `${pos(twoLow)}%`,
              width: `${pos(twoHigh) - pos(twoLow)}%`,
              height: 8,
              background: `${colors.accent}33`,
              border: `1px solid ${colors.accent}66`,
              borderRadius: 2,
            }}
            title="2σ range"
          />
        )}
        {oneLow != null && oneHigh != null && (
          <div
            style={{
              position: "absolute",
              top: 18,
              left: `${pos(oneLow)}%`,
              width: `${pos(oneHigh) - pos(oneLow)}%`,
              height: 12,
              background: `${colors.accent}55`,
              border: `1px solid ${colors.accent}`,
              borderRadius: 2,
            }}
            title="1σ range"
          />
        )}
        {gex != null && (
          <div
            style={{
              position: "absolute",
              top: 8,
              left: `${pos(gex)}%`,
              transform: "translateX(-50%)",
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              gap: 2,
            }}
            title="GEX magnetic level"
          >
            <span
              style={{
                ...numberStyle,
                fontSize: 10,
                color: colors.yellow,
                letterSpacing: 1,
                textTransform: "uppercase",
              }}
            >
              GEX
            </span>
            <div
              style={{
                width: 2,
                height: 32,
                background: colors.yellow,
              }}
            />
          </div>
        )}
      </div>

      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          ...numberStyle,
          fontSize: 11,
          color: colors.textDim,
          marginTop: 6,
        }}
      >
        <span>{scaleLo.toFixed(0)}</span>
        <span>
          <span style={{ color: `${colors.accent}` }}>■</span> 1σ ·{" "}
          <span style={{ color: `${colors.accent}88` }}>■</span> 2σ ·{" "}
          <span style={{ color: colors.yellow }}>│</span> GEX
        </span>
        <span>{scaleHi.toFixed(0)}</span>
      </div>
    </div>
  );
}

function HeadlineRow({ h }: { h: Headline }) {
  // h.sentiment is the FinBERT label string ("positive"/"negative"/"neutral").
  // h.sentiment_score is the signed number we actually want to display.
  // Coerce defensively — the API can return null, a string, or omit the field.
  const rawScore = h.sentiment_score ?? h.score ?? null;
  const score =
    rawScore !== null && rawScore !== undefined && !isNaN(Number(rawScore))
      ? Number(rawScore)
      : null;

  const rawConfidence = h.confidence;
  const confidence =
    rawConfidence !== null &&
    rawConfidence !== undefined &&
    !isNaN(Number(rawConfidence))
      ? Number(rawConfidence)
      : null;

  const sentColor =
    score === null
      ? colors.textMuted
      : score > 0.1
        ? colors.green
        : score < -0.1
          ? colors.red
          : colors.yellow;

  const title = h.title ?? h.headline ?? "(untitled)";

  return (
    <div
      style={{
        display: "flex",
        gap: 12,
        alignItems: "baseline",
        padding: "8px 0",
        borderBottom: `1px dashed ${colors.border}`,
      }}
    >
      <span
        style={{
          ...numberStyle,
          fontSize: 12,
          color: sentColor,
          minWidth: 56,
        }}
      >
        {score !== null ? fmtSigned(score, 2) : "—"}
      </span>
      <span style={{ flex: 1, fontFamily: mono, fontSize: 13 }}>
        {h.url ? (
          <a
            href={h.url}
            target="_blank"
            rel="noreferrer"
            style={{ color: colors.text, textDecoration: "none" }}
          >
            {title}
          </a>
        ) : (
          title
        )}
      </span>
      {confidence !== null && (
        <span
          style={{
            color: colors.textDim,
            fontSize: 11,
            fontFamily: mono,
          }}
        >
          {(confidence * 100).toFixed(0)}%
        </span>
      )}
      {h.source && (
        <span
          style={{
            color: colors.textDim,
            fontSize: 11,
            textTransform: "uppercase",
            letterSpacing: 1,
          }}
        >
          {h.source}
        </span>
      )}
    </div>
  );
}

function Headlines({ data }: { data: DailyVerdict }) {
  const semi = Array.isArray(data.top_headlines?.semi)
    ? data.top_headlines!.semi!.slice(0, 3)
    : [];
  const macro = Array.isArray(data.top_headlines?.macro)
    ? data.top_headlines!.macro!.slice(0, 3)
    : [];

  if (semi.length === 0 && macro.length === 0) {
    return (
      <div style={panelStyle}>
        <div style={panelLabelStyle}>Top Headlines</div>
        <div
          style={{
            color: colors.textMuted,
            fontFamily: mono,
            fontSize: 13,
            padding: "8px 0",
          }}
        >
          No headlines available
        </div>
      </div>
    );
  }

  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fit, minmax(360px, 1fr))",
        gap: 12,
      }}
    >
      {semi.length > 0 && (
        <div style={panelStyle}>
          <div style={panelLabelStyle}>Top Headlines · Semi</div>
          {semi.map((h, i) => (
            <HeadlineRow key={i} h={h} />
          ))}
        </div>
      )}
      {macro.length > 0 && (
        <div style={panelStyle}>
          <div style={panelLabelStyle}>Top Headlines · Macro</div>
          {macro.map((h, i) => (
            <HeadlineRow key={i} h={h} />
          ))}
        </div>
      )}
    </div>
  );
}

function EventRisk({ data }: { data: DailyVerdict }) {
  const events = data.event_names ?? [];
  if (!data.high_impact_event_today && events.length === 0) return null;
  return (
    <div
      style={{
        ...panelStyle,
        borderColor: colors.yellow,
      }}
    >
      <div style={{ ...panelLabelStyle, color: colors.yellow }}>
        Event Risk Today
      </div>
      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: 8,
          marginTop: 4,
        }}
      >
        {events.map((e, i) => (
          <span
            key={i}
            style={{
              ...numberStyle,
              fontSize: 12,
              padding: "4px 10px",
              border: `1px solid ${colors.yellow}`,
              color: colors.yellow,
              textTransform: "uppercase",
              letterSpacing: 1,
            }}
          >
            {e}
          </span>
        ))}
      </div>
    </div>
  );
}

function ReasonBlock({ data }: { data: DailyVerdict }) {
  if (!data.verdict_reason) return null;
  return (
    <div style={panelStyle}>
      <div style={panelLabelStyle}>Reason</div>
      <div
        style={{
          fontFamily: mono,
          fontSize: 14,
          color: colors.text,
          lineHeight: 1.55,
          whiteSpace: "pre-wrap",
        }}
      >
        {data.verdict_reason}
      </div>
    </div>
  );
}
