-- Supabase schema and Row Level Security (RLS) for Antigravity Remote
create table if not exists public.remote_devices (
  user_id uuid primary key references auth.users(id) on delete cascade,
  email text not null,
  tunnel_url text not null,
  device_name text default 'My PC',
  updated_at timestamptz default now()
);

alter table public.remote_devices enable row level security;

create policy "Users can view own device"
  on public.remote_devices for select
  using (auth.uid() = user_id);

create policy "Users can upsert own device"
  on public.remote_devices for insert
  with check (auth.uid() = user_id);

create policy "Users can update own device"
  on public.remote_devices for update
  using (auth.uid() = user_id);
