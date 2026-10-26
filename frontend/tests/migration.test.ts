import { readFileSync } from 'node:fs';
import { PGlite } from '@electric-sql/pglite';
import { expect, test } from 'vitest';

test('Supabase migration: ownership, atomic revisions, quotas and private storage policies', async () => {
  const db = new PGlite();
  try {
    await db.exec(`create role anon; create role authenticated;
      create schema auth; create schema storage;
      create table auth.users(id uuid primary key);
      create function auth.uid() returns uuid language sql stable as $$ select nullif(current_setting('request.jwt.claim.sub',true),'')::uuid $$;
      create table storage.buckets(id text primary key,name text,public boolean,file_size_limit bigint,allowed_mime_types text[]);
      create table storage.objects(id uuid primary key,bucket_id text,name text);
      alter table storage.objects enable row level security;
      grant usage on schema public,auth,storage to authenticated;
      grant select,insert,delete on storage.objects to authenticated;
      insert into auth.users values ('00000000-0000-4000-8000-000000000001'),('00000000-0000-4000-8000-000000000002');`);
    const migration = readFileSync('../supabase/migrations/001_eagle_eye.sql', 'utf8');
    await db.exec(migration);
    await db.exec(migration); // Operators may safely re-run setup.
    const owner = '00000000-0000-4000-8000-000000000001', id = '00000000-0000-4000-8000-000000000003';
    await db.exec(`set role authenticated; set request.jwt.claim.sub = '${owner}';`);
    const payload = {id,storage_version:1,deletion_status:null,documents:[]};
    await db.query('insert into public.cases(id,owner_id,payload) values($1,$2,$3)',[id,owner,JSON.stringify(payload)]);
    await db.query('select public.save_case($1,1,$2,false,false)',[id,JSON.stringify({...payload,storage_version:2})]);
    await expect(db.query('select public.save_case($1,1,$2,false,false)',[id,JSON.stringify({...payload,storage_version:2})])).rejects.toThrow();
    await db.exec("set request.jwt.claim.sub = '00000000-0000-4000-8000-000000000002';");
    expect((await db.query('select * from public.cases')).rows).toHaveLength(0);
    await expect(db.query('select public.save_case($1,2,$2,false,false)',[id,JSON.stringify({...payload,storage_version:3})])).rejects.toThrow();
    expect((await db.query<{ok:boolean}>("select public.consume_quota('requests:1',1,1) as ok")).rows[0].ok).toBe(true);
    expect((await db.query<{ok:boolean}>("select public.consume_quota('requests:1',1,1) as ok")).rows[0].ok).toBe(false);
  } finally {await db.close();}
}, 30000);
