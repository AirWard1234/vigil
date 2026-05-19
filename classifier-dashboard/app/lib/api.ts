export type Verdict = "GREEN" | "YELLOW" | "RED";

export type Headline = {
  title?: string;
  headline?: string;
  source?: string;
  url?: string;
  ticker?: string;
  sentiment?: string | number | null;
  sentiment_score?: number | string | null;
  confidence?: number | string | null;
  score?: number | string | null;
  weight?: number | null;
  published?: string;
  datetime?: string;
};

export type TopHeadlines = {
  semi?: Headline[];
  macro?: Headline[];
};

export type DailyVerdict = {
  id?: string;
  date: string;
  verdict: Verdict | null;
  strike_count: number | null;
  strikes_triggered: string[] | null;

  regime_label: string | null;
  regime_confidence: number | null;

  yield_bps_change: number | null;
  yield_accelerating: boolean | null;
  smh_vs_spy: number | null;
  smh_vs_qqq: number | null;
  semi_health_score: number | null;
  semi_health_label: string | null;
  vix_term_structure: string | null;
  realized_vs_implied: number | null;

  smh_iv: number | null;
  nvda_iv: number | null;
  smh_iv_elevated: boolean | null;
  nvda_iv_elevated: boolean | null;

  gex_value: number | null;
  gex_label: string | null;
  gex_key_level_mnq: number | null;

  semi_sentiment_score: number | null;
  macro_sentiment_score: number | null;
  top_headlines: TopHeadlines | null;

  high_impact_event_today: boolean | null;
  event_names: string[] | null;
  earnings_flag: boolean | null;
  guidance_cut_flag: boolean | null;

  expected_range_low: number | null;
  expected_range_high: number | null;
  one_sigma_low: number | null;
  one_sigma_high: number | null;

  verdict_reason: string | null;
  created_at?: string;
  stale?: boolean;

  bias_score: number | null;
  bias_label: string | null;
  bias_conviction: string | null;
  bias_reason: string | null;

  gap_label: string | null;
  gap_pct: number | null;
  open_hold: string | null;
  sweep_risk: string | null;
};

export type HistoryResponse = {
  count: number;
  days: number;
  verdicts: DailyVerdict[];
};

export type AccuracyStats = {
  total_days: number;
  reconciled_days: number;
  range_accuracy_expected: number | null;
  range_accuracy_1sigma: number | null;
  regime_accuracy: number | null;
  verdict_accuracy: number | null;
  green_days_correct: number | null;
  yellow_days_correct: number | null;
  red_days_correct: number | null;
  green_days_total?: number;
  yellow_days_total?: number;
  red_days_total?: number;
};

const API_URL = process.env.NEXT_PUBLIC_API_URL;

function requireApiUrl(): string {
  if (!API_URL) {
    throw new Error(
      "NEXT_PUBLIC_API_URL is not set. Configure it in .env.local or Vercel project settings.",
    );
  }
  return API_URL.replace(/\/+$/, "");
}

export async function fetchLatest(): Promise<DailyVerdict> {
  const res = await fetch(`${requireApiUrl()}/latest`, { cache: "no-store" });
  if (!res.ok) throw new Error(`latest fetch failed: ${res.status}`);
  return res.json();
}

export async function fetchHistory(days = 30): Promise<HistoryResponse> {
  const res = await fetch(`${requireApiUrl()}/history?days=${days}`, {
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`history fetch failed: ${res.status}`);
  return res.json();
}

export async function fetchAccuracy(days = 30): Promise<AccuracyStats> {
  const res = await fetch(`${requireApiUrl()}/accuracy?days=${days}`, {
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`accuracy fetch failed: ${res.status}`);
  return res.json();
}
