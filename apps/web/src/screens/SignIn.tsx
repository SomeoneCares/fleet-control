import { useState, type FormEvent } from "react";
import { api } from "../api/client";
import { useAuth } from "../lib/auth";
import { errorText } from "../lib/hooks";
import { Banner, Button, Field, INPUT, Icon, Spinner } from "../components/ui";

export function SignInScreen() {
  const { setMe } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      setMe(await api.login(email, password));
    } catch (err) {
      setError(errorText(err));
      setBusy(false);
    }
  }

  return (
    <div className="min-h-screen bg-container-low flex items-center justify-center px-4">
      <form onSubmit={(e) => void submit(e)} className="w-[420px] max-w-full bg-white border border-hairline rounded-card p-9 flex flex-col gap-6">
        <div className="flex items-center gap-2.5">
          <span className="size-7 rounded-control bg-primary text-on-primary flex items-center justify-center"><Icon name="logo" /></span>
          <div className="leading-[14px]">
            <div className="text-[14px] font-bold">Fleet Control</div>
            <div className="text-[11px] text-text-secondary">for Hermes Agent</div>
          </div>
        </div>
        <h1 className="text-title m-0">Sign in</h1>
        <Button variant="primary" icon="lock" disabled title="Single sign-on (OIDC) arrives after local accounts">Continue with corporate SSO</Button>
        <div className="flex items-center gap-3 text-small text-outline">
          <div className="flex-1 h-px bg-hairline" />or<div className="flex-1 h-px bg-hairline" />
        </div>
        <div>
          <Field label="Email">
            <input className={INPUT} type="email" autoComplete="username" required autoFocus value={email} onChange={(e) => setEmail(e.target.value)} placeholder="name@company.com" />
          </Field>
          <Field label="Password">
            <input className={INPUT} type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
          </Field>
          {error && <Banner tone="error" className="mb-4">{error}</Banner>}
          <Button type="submit" className="w-full" disabled={busy || !email || !password}>{busy && <Spinner />}Sign in</Button>
        </div>
        <div className="text-small text-text-secondary text-center">Fleet Control for Hermes Agent</div>
      </form>
    </div>
  );
}
