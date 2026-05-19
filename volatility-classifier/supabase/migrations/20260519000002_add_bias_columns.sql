alter table daily_verdicts
    add column if not exists bias_score float,
    add column if not exists bias_label text,
    add column if not exists bias_conviction text,
    add column if not exists bias_reason text;
