import { createClient, type SupabaseClient } from '@supabase/supabase-js';
import type { Capabilities } from './types';

// Production uses the same Vercel origin; only local Vite uses port 8000.
const base = (import.meta.env.VITE_API_BASE_URL ?? (import.meta.env.DEV ? 'http://localhost:8000' : '')).replace(/\/$/, '');
let client: SupabaseClient | null = null;
let configuration: Promise<Capabilities> | null = null;

export function capabilities(): Promise<Capabilities> {
  if (!configuration) configuration = fetch(base + '/api/capabilities').then(async response => {
    if (!response.ok) throw new Error('Backend setup is incomplete. Check the three Vercel environment variables and redeploy.');
    const data: Capabilities = await response.json();
    if (data.storage_mode === 'supabase') {
      if (!data.supabase_url || !data.supabase_publishable_key) throw new Error('Supabase public configuration is missing.');
      client = createClient(data.supabase_url, data.supabase_publishable_key, {auth: {detectSessionInUrl: true, persistSession: true, autoRefreshToken: true}});
    }
    return data;
  }).catch(error => {configuration = null; throw error;});
  return configuration;
}
export async function authClient(): Promise<SupabaseClient | null> {await capabilities(); return client;}

export async function token(): Promise<string> {
  const auth = await authClient();
  if (auth) return (await auth.auth.getSession()).data.session?.access_token || '';
  return localStorage.getItem('eagle-eye-session') || '';
}
export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const access = await token();
  if (access) headers.set('Authorization', `Bearer ${access}`);
  if (init.body && !(init.body instanceof FormData)) headers.set('Content-Type', 'application/json');
  let response: Response;
  try { response = await fetch(base + path, { ...init, headers }); }
  catch { throw new Error('Cannot reach the Eagle Eye backend. Check that it is running and the API URL is correct.'); }
  if (!response.ok) {
    const error = await response.json().catch(() => null);
    if (response.status === 413) throw new Error('Upload at most 3 MB of files per batch. Use smaller photos or fewer pages.');
    throw new Error(error?.error?.message || `Request failed (${response.status}). Please retry.`);
  }
  if (response.headers.get('content-type')?.includes('application/json')) return response.json();
  return await response.blob() as T;
}
export const post = <T,>(path: string, body: unknown) => api<T>(path, {method: 'POST', body: JSON.stringify(body)});
export async function connectLocal(): Promise<void> {
  if ((await capabilities()).storage_mode !== 'local') throw new Error('Sign in before starting a private case.');
  const result = await post<{access_token: string}>('/api/auth/local', {});
  localStorage.setItem('eagle-eye-session', result.access_token);
}
export async function signOut() {
  const auth = await authClient();
  if (auth) {const {error} = await auth.auth.signOut(); if (error) throw error;}
  localStorage.removeItem('eagle-eye-session');
}
export function download(name: string, data: Blob | string, type = 'text/plain;charset=utf-8') {
  const objectUrl = URL.createObjectURL(typeof data === 'string' ? new Blob([data], {type}) : data);
  const a = document.createElement('a'); a.href = objectUrl; a.download = name; a.click();
  setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
}
