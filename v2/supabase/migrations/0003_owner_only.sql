-- Only the owner's login can read or write. The owner's email lives in private.owner,
-- inserted by hand (not in this public repo). Anyone else who signs up sees nothing.
create schema if not exists private;
create table if not exists private.owner (email text primary key);

create or replace function public.is_owner() returns boolean
language sql stable security definer set search_path = '' as $$
  select exists (select 1 from private.owner where email = (auth.jwt() ->> 'email'))
$$;

do $$ declare t text;
begin
  foreach t in array array['accounts','budgets','commitments','settings','raw_alerts','transactions','payee_memory'] loop
    execute format('drop policy if exists owner_all on %I', t);
    execute format('create policy owner_all on %I for all to authenticated using (public.is_owner()) with check (public.is_owner())', t);
  end loop;
end $$;

-- Live updates in the app when a new SMS lands.
alter publication supabase_realtime add table transactions;
