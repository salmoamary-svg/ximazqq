-- An account whose SMS we no longer receive. Money moved into it is treated as spent
-- at the moment it leaves a tracked account, since what it buys will never be seen.
alter table public.accounts add column if not exists tracked boolean not null default true;
update public.accounts set tracked = false where id = 'stc';
