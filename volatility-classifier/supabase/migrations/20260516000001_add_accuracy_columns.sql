alter table daily_verdicts
    add column if not exists actual_high float,
    add column if not exists actual_low float,
    add column if not exists actual_close float,
    add column if not exists actual_realized_vol float,
    add column if not exists range_hit_expected boolean,
    add column if not exists range_hit_1sigma boolean,
    add column if not exists regime_match boolean,
    add column if not exists verdict_was_correct boolean,
    add column if not exists reconciled_at timestamptz;

create index if not exists daily_verdicts_reconciled_at_idx
    on daily_verdicts (reconciled_at);
