-- Saad Money v2 — core schema.
-- One row per bank SMS in raw_alerts (the source of truth, never deleted);
-- one classified row per SMS in transactions. Everything on screen derives from these.

create table accounts (
  id         text primary key,          -- 'bsf' | 'stc'
  name       text not null,
  bank       text not null,
  markers    text[] not null default '{}',  -- strings that identify this account in an SMS: '0125', '8738', '208'
  sort       int  not null default 0
);

create table budgets (
  id    text primary key,
  name  text not null,
  cap   numeric(12,2) not null,
  icon  text,
  sort  int not null default 0
);

create table commitments (
  id      text primary key,
  name    text not null,
  amount  numeric(12,2) not null,
  payee   text,                          -- hint for the classifier ("Musaned", "STC", "Alinma ...")
  sort    int not null default 0
);

create table settings (
  key   text primary key,
  value jsonb not null
);

-- Every SMS that reaches us, verbatim. hash dedupes Shortcut double-fires.
create table raw_alerts (
  id           bigserial primary key,
  hash         text not null unique,
  text         text not null,
  sender       text,
  received_at  timestamptz not null default now(),
  processed_at timestamptz,
  error        text
);

create type tx_kind as enum ('salary','income','spend','commitment','transfer_out','transfer_in','ignore');

create table transactions (
  id            bigserial primary key,
  raw_id        bigint references raw_alerts(id) on delete cascade,
  kind          tx_kind not null,
  account_id    text references accounts(id),
  amount        numeric(12,2) not null default 0,   -- principal, excluding fee
  fee           numeric(12,2) not null default 0,
  counterparty  text,
  budget_id     text references budgets(id),
  commitment_id text references commitments(id),
  pair_id       bigint references transactions(id), -- the other leg of a transfer
  confidence    real not null default 1,
  confirmed     boolean not null default true,      -- false = needs a tap in the app
  occurred_at   timestamptz not null default now(),
  note          text,
  source        text not null default 'auto',       -- 'auto' | 'me'
  created_at    timestamptz not null default now()
);
create index on transactions (occurred_at desc);
create index on transactions (confirmed) where not confirmed;

-- What the user taught us: "this payee is always this budget/commitment".
create table payee_memory (
  payee         text primary key,                   -- normalized counterparty
  kind          tx_kind not null,
  budget_id     text references budgets(id),
  commitment_id text references commitments(id),
  taught_at     timestamptz not null default now()
);

-- RLS: only a signed-in user (there is exactly one) or the service role can touch anything.
alter table accounts      enable row level security;
alter table budgets       enable row level security;
alter table commitments   enable row level security;
alter table settings      enable row level security;
alter table raw_alerts    enable row level security;
alter table transactions  enable row level security;
alter table payee_memory  enable row level security;

do $$ declare t text;
begin
  foreach t in array array['accounts','budgets','commitments','settings','raw_alerts','transactions','payee_memory'] loop
    execute format('create policy owner_all on %I for all to authenticated using (true) with check (true)', t);
  end loop;
end $$;
