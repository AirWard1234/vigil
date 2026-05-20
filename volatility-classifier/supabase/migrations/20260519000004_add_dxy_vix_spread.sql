alter table daily_verdicts
    add column if not exists dxy_change float,
    add column if not exists dxy_label text,
    add column if not exists vix_spread float,
    add column if not exists vix_spread_label text;
