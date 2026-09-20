"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { getStoredUser, login } from "@/lib/api";
import { useLocalClock } from "@/lib/use-clock";
import { PipelineStrip } from "@/components/pipeline";

export default function LoginPage() {
  const router = useRouter();
  const clock = useLocalClock();
  const [email, setEmail] = useState("admin@aeris.io");
  const [password, setPassword] = useState("admin123");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (getStoredUser()) router.replace("/dashboard");
  }, [router]);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email, password);
      router.replace("/dashboard");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
    } finally {
      setBusy(false);
    }
  }

  const field =
    "w-full border-b border-line bg-transparent py-3 font-mono text-sm text-paper outline-none transition-colors focus:border-volt";

  return (
    <div className="grid min-h-screen bg-ink bg-blueprint lg:grid-cols-[1.35fr_1fr]">
      {/* ---------------------------------------------------- editorial half */}
      <section className="flex flex-col justify-between border-line px-8 py-10 lg:border-r lg:px-14 lg:py-14">
        <header className="flex items-center justify-between">
          <div className="flex items-baseline gap-px">
            <span className="text-2xl font-black tracking-[-0.04em] text-paper">AERIS</span>
            <span className="text-volt">®</span>
          </div>
          <div className="text-right">
            <div className="micro text-faint">Local time</div>
            <div className="font-mono text-[13px] tabular-nums text-paper">{clock}</div>
          </div>
        </header>

        <div className="py-12">
          <div className="micro text-muted">
            Aerial &amp; Industrial Environmental Risk Intelligence System
          </div>
          <h1 className="display mt-7 text-[clamp(2.4rem,7.5vw,5.6rem)] font-medium">
            Sense the air.
            <br />
            <span className="font-black text-volt">Rank</span> the risk.
            <br />
            Warn the floor.
          </h1>
          <p className="mt-8 max-w-xl text-sm leading-relaxed text-muted">
            Distributed ESP32 nodes stream temperature, combustible gas, CO, smoke and flame
            state over MQTT. AERIS validates every frame, engineers the features, then runs a
            three-level safety engine — hard rules, trend analysis, machine-learning
            prediction — to localise the hazard to a zone and raise the alert before it
            escalates.
          </p>
        </div>

        <div>
          <PipelineStrip />
          <p className="micro mt-6 max-w-2xl text-faint">
            Prototype · simulated facility geometry · decision-support only — not certified
            fire &amp; gas detection equipment.
          </p>
        </div>
      </section>

      {/* --------------------------------------------------------- auth half */}
      <section className="flex flex-col justify-center border-t border-line bg-ink-2 px-8 py-14 lg:border-t-0 lg:px-14">
        <div className="mx-auto w-full max-w-sm">
          <div className="micro text-volt">Restricted console</div>
          <h2 className="display mt-5 text-[clamp(1.6rem,3.4vw,2.4rem)] font-black">
            Operator
            <br />
            Sign-in
          </h2>

          <form onSubmit={onSubmit} className="mt-10">
            <label className="micro block text-faint" htmlFor="email">
              Email
            </label>
            <input
              id="email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className={field}
              autoComplete="username"
              required
            />

            <label className="micro mt-7 block text-faint" htmlFor="password">
              Password
            </label>
            <input
              id="password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className={field}
              autoComplete="current-password"
              required
            />

            {error && (
              <p className="mt-5 border border-scarlet/50 bg-scarlet/10 px-3 py-2 text-xs text-scarlet">
                {error}
              </p>
            )}

            <button
              type="submit"
              disabled={busy}
              className="micro mt-9 w-full bg-volt px-4 py-4 font-bold text-ink transition-opacity hover:opacity-85 disabled:opacity-50"
            >
              {busy ? "Authenticating…" : "Enter console →"}
            </button>
          </form>

          <div className="mt-10 border-t border-line pt-5">
            <div className="micro text-faint">Seeded accounts</div>
            <div className="mt-3 space-y-2 font-mono text-[11px] text-muted">
              <div className="flex justify-between">
                <span>admin@aeris.io</span>
                <span className="text-faint">admin123</span>
              </div>
              <div className="flex justify-between">
                <span>safety@aeris.io</span>
                <span className="text-faint">safety123</span>
              </div>
              <div className="flex justify-between">
                <span>operator@aeris.io</span>
                <span className="text-faint">operator123</span>
              </div>
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}
