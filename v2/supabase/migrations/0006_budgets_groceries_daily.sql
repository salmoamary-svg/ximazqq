-- 2026-09-28: groceries become a spending budget (owner pays them directly now), Food & coffee
-- becomes Coffee & daily. Sister E.'s remaining payoff is held in a separate account and is no
-- longer a monthly commitment; the credit card is paid off. Loan installment rises to 9,300.
-- (Row-level edits for existing transactions were applied by hand at the same time.)
update public.budgets set sort = sort + 1 where sort >= 1;
insert into public.budgets (id, name, cap, icon, sort) values ('groceries', 'Groceries', 1000, '🛒', 1) on conflict (id) do nothing;
update public.budgets set name = 'Coffee & daily', icon = '☕' where id = 'food';
update public.commitments set amount = 9300, name = 'Bank loan' where id = 'loan';
update public.commitments set amount = 1500, name = 'Domestic helper' where id = 'maid';
delete from public.commitments where id in ('cc', 'sisE', 'groc');
