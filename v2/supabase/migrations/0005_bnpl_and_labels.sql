-- Tabby / Tamara instalments are a monthly commitment, not budget spending.
insert into public.commitments (id, name, amount, payee, sort) values ('bnpl', 'BNPL (Tabby / Tamara)', 2313, 'Tabby / Tamara', 9) on conflict (id) do nothing;
-- Free-text "what was it" for a spend that fits no budget ("Something else…" in the app).
alter table public.transactions add column if not exists label text;
