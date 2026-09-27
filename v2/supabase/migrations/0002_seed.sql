-- Seed from the live v1 data.json (2026-09-27). Budgets are placeholders to be renamed in the app.
insert into accounts (id, name, bank, markers, sort) values
  ('bsf', 'BSF',      'Banque Saudi Fransi', '{0125,8738}', 0),
  ('stc', 'STC Bank', 'STC Bank',            '{208}',       1);

insert into commitments (id, name, amount, payee, sort) values
  ('loan',  'Bank loan',            7027, 'قسط',                 0),
  ('phone', 'Phone / telecom',      1720, 'STC postpaid',        1),
  ('maid',  'Housemaid (Musaned)',  1500, 'Musaned',             2),
  ('groc',  'Groceries → wife',     1000, null,                  3),
  ('elec',  'Electricity',           527, 'SEC / كهرباء',        4),
  ('subs',  'Subscriptions',        1028, null,                  5),
  ('sisE',  'Sister E. payoff',     4000, null,                  6),
  ('sisA',  'Sister A.',            1000, null,                  7),
  ('cc',    'Credit-card paydown',   930, 'تسديد بطاقة',        8);

insert into budgets (id, name, cap, icon, sort) values
  ('food',     'Food & coffee', 1500, '🍔', 0),
  ('shopping', 'Shopping',      1000, '🛍️', 1),
  ('travel',   'Travel',         800, '✈️', 2),
  ('fun',      'Fun & games',    500, '🎮', 3),
  ('family',   'Family',        1000, '👨‍👩‍👧', 4),
  ('other',    'Other',          500, '📦', 5);

insert into settings (key, value) values
  ('salary',      '27932'),
  ('payday_day',  '27'),
  ('owner_names', '["ALMOAMARY","المعمري"]');
