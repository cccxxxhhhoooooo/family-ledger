-- 家庭记账表。在 Supabase 控制台的 SQL Editor 中整段执行。
-- 使用者和 app.py 里的下拉选项保持一致；要加人时，同时改这里和界面列表。

create table if not exists public.bills (
    id uuid primary key,
    bill_date date not null,
    category text not null,
    amount numeric(12, 2) not null check (amount > 0),
    note text not null default '',
    owner text not null,
    created_at timestamptz not null default now()
);

create index if not exists bills_owner_date_idx
    on public.bills (owner, bill_date desc);

alter table public.bills enable row level security;

-- 应用放在 Streamlit 服务端，使用 anon key 访问。
-- 不要把 service_role key 写进 secrets。
-- 先去掉旧限制，再改人名，最后加上新限制。
-- 顺序反过来时，旧约束会拒绝「老公」。
alter table public.bills drop constraint if exists bills_owner_check;

update public.bills
set owner = '老公'
where owner not in ('老公', '美女');

alter table public.bills add constraint bills_owner_check
    check (owner in ('老公', '美女'));

grant select, insert, delete on public.bills to anon, authenticated;

drop policy if exists "family read bills" on public.bills;
create policy "family read bills"
    on public.bills
    for select
    to anon, authenticated
    using (true);

drop policy if exists "family insert bills" on public.bills;
create policy "family insert bills"
    on public.bills
    for insert
    to anon, authenticated
    with check (true);

drop policy if exists "family delete bills" on public.bills;
create policy "family delete bills"
    on public.bills
    for delete
    to anon, authenticated
    using (true);
