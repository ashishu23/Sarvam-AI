import { useState } from 'react';
import { authClient } from '../lib/api';

export function SignIn({done, cancel}: {done: () => void; cancel: () => void}) {
  const [email, setEmail] = useState(''), [password, setPassword] = useState('');
  const [mode, setMode] = useState<'login' | 'signup'>('login');
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [notice, setNotice] = useState('');
  const submit = async (event: React.FormEvent) => {
    event.preventDefault(); setBusy(true); setError(''); setNotice('');
    try {
      const client = await authClient();
      if (!client) throw new Error('Cloud sign-in is not enabled in local mode.');
      const result = mode === 'login'
        ? await client.auth.signInWithPassword({email, password})
        : await client.auth.signUp({email, password, options: {emailRedirectTo: window.location.origin}});
      if (result.error) throw result.error;
      if (result.data.session) done();
      else setNotice('Check your email to confirm your account, then sign in here.');
    } catch (e) {setError(e instanceof Error ? e.message : 'Sign-in failed. Try again.');}
    finally {setBusy(false);}
  };
  return <section className="card" aria-labelledby="sign-in-title"><h2 id="sign-in-title">{mode === 'login' ? 'Sign in to your private cases' : 'Create your account'}</h2><p>Use the same account to return to your cases on another device.</p><form onSubmit={e => void submit(e)}><label>Email<input type="email" required autoComplete="email" value={email} onChange={e => setEmail(e.target.value)}/></label><label>Password<input type="password" required minLength={8} autoComplete={mode === 'login' ? 'current-password' : 'new-password'} value={password} onChange={e => setPassword(e.target.value)}/></label>{error && <p role="alert" className="error">{error}</p>}{notice && <p role="status" className="notice">{notice}</p>}<div className="button-row"><button disabled={busy} type="submit">{busy ? 'Please wait…' : mode === 'login' ? 'Sign in' : 'Create account'}</button><button type="button" className="secondary" disabled={busy} onClick={() => {setMode(mode === 'login' ? 'signup' : 'login');setError('');setNotice('');}}>{mode === 'login' ? 'Create an account' : 'I already have an account'}</button><button type="button" className="secondary" onClick={cancel}>Cancel</button></div></form></section>;
}
