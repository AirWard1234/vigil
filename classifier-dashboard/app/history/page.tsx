import { fetchHistory, type DailyVerdict } from "../lib/api";
import { colors, mono, panelLabelStyle, panelStyle } from "../lib/theme";
import HistoryTable from "./HistoryTable";

export const dynamic = "force-dynamic";

export default async function HistoryPage() {
  let verdicts: DailyVerdict[] = [];
  let error: string | null = null;
  try {
    const resp = await fetchHistory(60);
    verdicts = resp.verdicts ?? [];
  } catch (e) {
    error = e instanceof Error ? e.message : String(e);
  }

  if (error) {
    return (
      <div style={{ ...panelStyle, color: colors.red }}>
        <div style={panelLabelStyle}>History unavailable</div>
        <div style={{ fontFamily: mono, fontSize: 13 }}>{error}</div>
      </div>
    );
  }

  return <HistoryTable verdicts={verdicts} />;
}
