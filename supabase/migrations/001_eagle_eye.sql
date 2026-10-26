-- Run once in the Supabase SQL editor as the project owner. No service key is
-- required by the application. Re-running this script is safe.
begin;

create table if not exists public.cases (
    id uuid primary key,
    owner_id uuid not null references auth.users(id) on delete cascade,
    version integer not null default 1 check (version > 0),
    deleted boolean not null default false,
    payload jsonb not null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint cases_payload_object check (jsonb_typeof(payload) = 'object'),
    constraint cases_payload_identity check (payload->>'id' = id::text),
    constraint cases_payload_version check ((payload->>'storage_version')::integer = version),
    constraint cases_payload_size check (octet_length(payload::text) <= 3200000)
);
create index if not exists cases_owner_updated on public.cases(owner_id, updated_at desc);
alter table public.cases enable row level security;
revoke all on public.cases from anon, authenticated;
grant select, insert on public.cases to authenticated;
drop policy if exists cases_read_own on public.cases;
create policy cases_read_own on public.cases for select to authenticated
    using ((select auth.uid()) = owner_id);
drop policy if exists cases_create_own on public.cases;
create policy cases_create_own on public.cases for insert to authenticated
    with check ((select auth.uid()) = owner_id and version = 1 and deleted = false
                and payload->>'deletion_status' is null);

-- Only this atomic function updates a case. Owners cannot rewrite the owner,
-- bypass the expected version, or resurrect a tombstone through REST writes.
create or replace function public.save_case(
    p_id uuid, p_expected integer, p_payload jsonb, p_deleted boolean, p_cleanup boolean default false
) returns void language plpgsql security definer set search_path = '' as $$
declare
    current_row public.cases%rowtype;
begin
    select * into current_row from public.cases
    where id = p_id and owner_id = auth.uid() for update;
    if not found then
        raise exception 'Case not found or access denied' using errcode = '42501';
    end if;
    if current_row.version <> p_expected then
        raise exception 'Stale case version' using errcode = '40001';
    end if;
    if current_row.deleted and (not p_cleanup or not p_deleted) then
        raise exception 'Case was deleted' using errcode = '40001';
    end if;
    if current_row.payload->>'deletion_status' = 'complete' then
        raise exception 'Cleanup already completed' using errcode = '40001';
    end if;
    if p_payload->>'id' is distinct from p_id::text
        or (p_payload->>'storage_version')::integer is distinct from p_expected + 1
        or p_deleted is distinct from coalesce(p_payload->>'deletion_status' in ('pending','complete'), false) then
        raise exception 'Invalid case metadata' using errcode = '22023';
    end if;
    update public.cases set payload = p_payload, version = p_expected + 1,
        deleted = p_deleted, updated_at = now() where id = p_id;
end;
$$;
revoke all on function public.save_case(uuid, integer, jsonb, boolean, boolean) from public, anon;
grant execute on function public.save_case(uuid, integer, jsonb, boolean, boolean) to authenticated;

create table if not exists public.usage_counters (
    owner_id uuid not null references auth.users(id) on delete cascade,
    bucket text not null,
    used bigint not null check (used >= 0),
    updated_at timestamptz not null default now(),
    primary key (owner_id, bucket)
);
alter table public.usage_counters enable row level security;
revoke all on public.usage_counters from anon, authenticated;
create or replace function public.consume_quota(p_bucket text, p_amount integer, p_limit integer)
returns boolean language plpgsql security definer set search_path = '' as $$
declare
    changed integer;
    ceiling integer;
begin
    if auth.uid() is null or p_amount <= 0 or p_limit <= 0 or length(p_bucket) > 80 then
        raise exception 'Invalid quota request' using errcode = '22023';
    end if;
    ceiling := case split_part(p_bucket, ':', 1)
        when 'requests' then 60 when 'pages' then 30 when 'audio' then 300
        when 'case_creation' then 30 else 0 end;
    if ceiling = 0 or p_bucket !~ '^[a-z_]+:[0-9]+$' then
        raise exception 'Invalid quota bucket' using errcode = '22023';
    end if;
    if p_amount > least(p_limit, ceiling) then return false; end if;
    insert into public.usage_counters(owner_id, bucket, used)
        values(auth.uid(), p_bucket, p_amount)
    on conflict(owner_id, bucket) do update
        set used = public.usage_counters.used + p_amount, updated_at = now()
        where public.usage_counters.used + p_amount <= least(p_limit, ceiling);
    get diagnostics changed = row_count;
    return changed = 1;
end;
$$;
revoke all on function public.consume_quota(text, integer, integer) from public, anon;
grant execute on function public.consume_quota(text, integer, integer) to authenticated;

insert into storage.buckets(id, name, public, file_size_limit, allowed_mime_types)
values ('case-documents', 'case-documents', false, 26214400,
        array['application/pdf','image/jpeg','image/png','image/webp'])
on conflict(id) do update set public = false, file_size_limit = excluded.file_size_limit,
    allowed_mime_types = excluded.allowed_mime_types;

-- A private object must be declared in the caller's case before it is written.
-- This also prevents linking an object from a different case into a source.
create or replace function public.can_access_case_object(object_name text, for_cleanup boolean default false)
returns boolean language sql stable security invoker set search_path = '' as $$
    select exists (
        select 1 from public.cases c
        where c.owner_id = auth.uid()
        and split_part(object_name, '/', 1) = auth.uid()::text
        and split_part(object_name, '/', 2) = c.id::text
        and (for_cleanup or not c.deleted)
        and exists (
            select 1 from jsonb_array_elements(coalesce(c.payload->'documents', '[]'::jsonb)) d
            where split_part(object_name, '/', 3) = d->>'id'
              and (d->>'processed_path' = object_name or exists (
                  select 1 from jsonb_array_elements(coalesce(d->'originals', '[]'::jsonb)) o
                  where o->>'path' = object_name
              ))
        )
    );
$$;
revoke all on function public.can_access_case_object(text, boolean) from public, anon;
grant execute on function public.can_access_case_object(text, boolean) to authenticated;
drop policy if exists eagle_documents_read on storage.objects;
drop policy if exists eagle_documents_insert on storage.objects;
drop policy if exists eagle_documents_delete on storage.objects;
create policy eagle_documents_read on storage.objects for select to authenticated
    using (bucket_id = 'case-documents' and public.can_access_case_object(name, true));
create policy eagle_documents_insert on storage.objects for insert to authenticated
    with check (bucket_id = 'case-documents' and public.can_access_case_object(name, false));
create policy eagle_documents_delete on storage.objects for delete to authenticated
    using (bucket_id = 'case-documents' and public.can_access_case_object(name, true));
-- Tombstoned objects remain owner-readable only until cleanup completes because
-- Storage DELETE also requires SELECT. API evidence access is revoked immediately.
-- Cleanup removes files before erasing the document index, then keeps an empty tombstone.

commit;
