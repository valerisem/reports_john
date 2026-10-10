-- Finished weeks of pipeline history for the report (see app/weekly_cache.py).
-- One row per week, ending Sunday. Amounts are per currency, e.g. {"GBP": 1200, "USD": 500}.
create table if not exists public.report_weekly_pipeline (
  week_ending date primary key,
  all_deals integer not null,
  all_value jsonb not null default '{}'::jsonb,
  all_weighted jsonb not null default '{}'::jsonb,
  new_deals integer not null,
  new_value jsonb not null default '{}'::jsonb,
  new_weighted jsonb not null default '{}'::jsonb,
  computed_at timestamptz not null default now()
);

alter table public.report_weekly_pipeline enable row level security;
create policy report_weekly_pipeline_select on public.report_weekly_pipeline for select using (true);
create policy report_weekly_pipeline_insert on public.report_weekly_pipeline for insert with check (true);

-- To make the report rebuild every week (e.g. after changing stage probabilities):
--   delete from public.report_weekly_pipeline;
