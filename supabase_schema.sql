-- Run this in the Supabase SQL editor of the new project.

create table public.participants (
    id uuid primary key,
    name text not null,
    set_order jsonb,
    condition_orders jsonb,
    user_agent text,
    completed boolean not null default false,
    completed_at timestamptz,
    created_at timestamptz not null default now()
);

create table public.responses (
    id bigint generated always as identity primary key,
    participant_id uuid not null,
    participant_name text,
    set_id text not null,
    condition text not null,          -- intentcut_s2 | funclip | timechat | random
    file_key text,                    -- v1..v4 (blinded file name)
    set_index int,                    -- 0-based position in this participant's set order
    video_index int,                  -- 0-based position within the set
    q1 int check (q1 between 1 and 7),
    q2 int check (q2 between 1 and 7),
    q3 int check (q3 between 1 and 7),
    watch_seconds real,
    created_at timestamptz not null default now()
);

-- RLS: anon key may only insert (and mark own participant complete). No reads.
alter table public.participants enable row level security;
alter table public.responses enable row level security;

create policy "anon insert participants" on public.participants
    for insert to anon with check (true);
create policy "anon complete participants" on public.participants
    for update to anon using (true) with check (true);
create policy "anon insert responses" on public.responses
    for insert to anon with check (true);
