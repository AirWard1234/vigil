-- Forward-looking earnings calendar — Tier 1/2 Nasdaq names reporting in the
-- next 3 trading days. earnings_today / earnings_tomorrow hold ticker lists;
-- upcoming_earnings holds the full per-name detail (tier, report time, EPS
-- estimate) the dashboards and Telegram alert render.
alter table daily_verdicts
    add column if not exists upcoming_earnings jsonb,
    add column if not exists earnings_today jsonb,
    add column if not exists earnings_tomorrow jsonb,
    add column if not exists earnings_today_tier1 boolean;
