alter table daily_verdicts
    add column if not exists gap_label text,
    add column if not exists gap_pct float,
    add column if not exists open_hold text,
    add column if not exists sweep_risk text;
