/**
 * AERIS Landing Page — Hero + Features + Loading Animation.
 *
 * The page opens with a branded loading sequence, then reveals
 * the full landing content with a CTA to enter the dashboard.
 */
"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { getStoredUser } from "@/lib/api";

/* ── Loading Animation ─────────────────────────────────────────────── */

function LoadingOverlay({ onComplete }: { onComplete: () => void }) {
  const [phase, setPhase] = useState(0); // 0=logo, 1=text, 2=scanning, 3=ready

  useEffect(() => {
    const timers = [
      setTimeout(() => setPhase(1), 600),
      setTimeout(() => setPhase(2), 1400),
      setTimeout(() => setPhase(3), 2400),
      setTimeout(onComplete, 3000),
    ];
    return () => timers.forEach(clearTimeout);
  }, [onComplete]);

  return (
    <div className="fixed inset-0 z-50 bg-ink flex items-center justify-center">
      {/* Blueprint grid background */}
      <div className="absolute inset-0 bg-blueprint opacity-30" />

      {/* Scan line effect */}
      <div className="absolute inset-0 overflow-hidden pointer-events-none">
        <div
          className="absolute left-0 right-0 h-px bg-gradient-to-r from-transparent via-volt to-transparent"
          style={{
            animation: "scanline 2s ease-in-out infinite",
            opacity: phase >= 2 ? 0.6 : 0,
          }}
        />
      </div>

      <div className="relative text-center">
        {/* Logo mark */}
        <div className="mb-8">
          <div
            className={`inline-flex items-center justify-center w-24 h-24 border transition-all duration-700 ${
              phase >= 1
                ? "border-volt scale-100 opacity-100"
                : "border-muted scale-75 opacity-0"
            }`}
          >
            <svg
              viewBox="0 0 48 48"
              className={`w-12 h-12 transition-all duration-500 ${
                phase >= 1 ? "text-volt" : "text-muted"
              }`}
              fill="none"
              stroke="currentColor"
              strokeWidth={1.5}
            >
              {/* Hexagonal shield shape */}
              <path d="M24 4L42 14V34L24 44L6 34V14L24 4Z" />
              {/* Inner wave lines (gas detection) */}
              <path
                d="M12 24C16 18 20 30 24 24C28 18 32 30 36 24"
                className={`transition-all duration-700 ${
                  phase >= 2 ? "opacity-100" : "opacity-0"
                }`}
              />
              {/* Center dot */}
              <circle
                cx="24"
                cy="24"
                r="2"
                className={`transition-all duration-500 ${
                  phase >= 2 ? "fill-volt opacity-100" : "opacity-0"
                }`}
              />
            </svg>
          </div>
        </div>

        {/* Title */}
        <div
          className={`transition-all duration-700 delay-200 ${
            phase >= 1 ? "opacity-100 translate-y-0" : "opacity-0 translate-y-4"
          }`}
        >
          <h1 className="display text-5xl md:text-7xl font-bold tracking-tight">
            <span className="text-paper">AER</span>
            <span className="text-volt">IS</span>
          </h1>
        </div>

        {/* Subtitle */}
        <div
          className={`mt-4 transition-all duration-700 delay-300 ${
            phase >= 1 ? "opacity-100 translate-y-0" : "opacity-0 translate-y-4"
          }`}
        >
          <p className="font-mono text-xs text-muted tracking-[0.3em] uppercase">
            Aerial & Industrial Environmental Risk Intelligence System
          </p>
        </div>

        {/* Status line */}
        <div
          className={`mt-8 transition-all duration-500 ${
            phase >= 2 ? "opacity-100" : "opacity-0"
          }`}
        >
          <div className="font-mono text-[10px] text-faint tracking-wider">
            {phase < 3 ? (
              <span className="flex items-center gap-2 justify-center">
                <span className="inline-block w-1.5 h-1.5 bg-volt rounded-full blink" />
                INITIALIZING SENSOR ARRAY...
              </span>
            ) : (
              <span className="text-volt">SYSTEM READY</span>
            )}
          </div>
        </div>

        {/* Progress bar */}
        <div
          className={`mt-6 w-48 mx-auto transition-opacity duration-500 ${
            phase >= 2 ? "opacity-100" : "opacity-0"
          }`}
        >
          <div className="h-px bg-line-soft relative overflow-hidden">
            <div
              className="absolute inset-y-0 left-0 bg-volt transition-all duration-1000 ease-out"
              style={{ width: `${Math.min(100, (phase / 3) * 100)}%` }}
            />
          </div>
        </div>
      </div>
    </div>
  );
}

/* ── Feature Card ──────────────────────────────────────────────────── */

function FeatureCard({
  index,
  title,
  description,
  icon,
}: {
  index: string;
  title: string;
  description: string;
  icon: React.ReactNode;
}) {
  return (
    <div className="group relative p-6 border border-line hover:border-accent/30 transition-colors duration-300">
      {/* Corner ticks */}
      <div className="absolute top-0 left-0 w-2 h-2 border-t border-l border-line group-hover:border-volt transition-colors" />
      <div className="absolute bottom-0 right-0 w-2 h-2 border-b border-r border-line group-hover:border-volt transition-colors" />

      <div className="font-mono text-micro text-muted tracking-[0.3em] mb-3">
        {index}
      </div>
      <div className="text-volt mb-3">{icon}</div>
      <h3 className="text-type text-sm uppercase tracking-wider mb-2">
        {title}
      </h3>
      <p className="font-mono text-xs text-muted leading-relaxed">
        {description}
      </p>
    </div>
  );
}

/* ── Stat Block ────────────────────────────────────────────────────── */

function StatBlock({ value, label }: { value: string; label: string }) {
  return (
    <div className="text-center">
      <div className="text-3xl md:text-4xl font-bold text-paper">{value}</div>
      <div className="font-mono text-[10px] text-muted tracking-[0.2em] uppercase mt-1">
        {label}
      </div>
    </div>
  );
}

/* ── Main Landing Page ─────────────────────────────────────────────── */

export default function LandingPage() {
  const router = useRouter();
  const [loaded, setLoaded] = useState(false);
  const [showContent, setShowContent] = useState(false);

  // Check if user is already logged in
  useEffect(() => {
    const user = getStoredUser();
    if (user) {
      // Logged in — redirect straight to dashboard
      router.push("/dashboard");
    }
  }, [router]);

  const handleLoadingComplete = () => {
    setLoaded(true);
    setTimeout(() => setShowContent(true), 100);
  };

  return (
    <div className="min-h-screen bg-ink">
      {/* Loading Overlay */}
      {!loaded && <LoadingOverlay onComplete={handleLoadingComplete} />}

      {/* Main Content */}
      <div
        className={`transition-opacity duration-1000 ${
          showContent ? "opacity-100" : "opacity-0"
        }`}
      >
        {/* Navigation */}
        <nav className="fixed top-0 left-0 right-0 z-40 bg-ink/80 backdrop-blur-sm border-b border-line">
          <div className="max-w-7xl mx-auto px-6 py-4 flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="w-8 h-8 border border-volt flex items-center justify-center">
                <svg
                  viewBox="0 0 24 24"
                  className="w-4 h-4 text-volt"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth={1.5}
                >
                  <path d="M12 2L21 7V17L12 22L3 17V7L12 2Z" />
                </svg>
              </div>
              <span className="font-mono text-xs tracking-[0.2em] text-paper">
                AERIS
              </span>
            </div>
            <div className="flex items-center gap-6">
              <a href="#features" className="font-mono text-xs text-muted hover:text-paper transition-colors">
                FEATURES
              </a>
              <a href="#architecture" className="font-mono text-xs text-muted hover:text-paper transition-colors">
                ARCHITECTURE
              </a>
              <button
                onClick={() => router.push("/login")}
                className="font-mono text-xs bg-volt text-ink px-4 py-2 hover:bg-volt/90 transition-colors"
              >
                ENTER SYSTEM
              </button>
            </div>
          </div>
        </nav>

        {/* Hero Section */}
        <section className="relative min-h-screen flex items-center justify-center pt-20">
          {/* Blueprint grid */}
          <div className="absolute inset-0 bg-blueprint opacity-20" />

          {/* Corner decorations */}
          <div className="absolute top-24 left-8 w-16 h-16 border-t border-l border-line/50" />
          <div className="absolute bottom-8 right-8 w-16 h-16 border-b border-r border-line/50" />

          <div className="relative z-10 text-center px-6 max-w-5xl">
            {/* Micro label */}
            <div className="font-mono text-micro text-volt tracking-[0.4em] mb-6">
              INDUSTRIAL SAFETY PLATFORM
            </div>

            {/* Main headline */}
            <h1 className="display text-6xl md:text-8xl lg:text-9xl font-bold mb-6">
              <span className="text-paper">THE FLOOR</span>
              <br />
              <span className="text-paper">IS </span>
              <span className="text-volt">SENSED.</span>
            </h1>

            {/* Subheadline */}
            <p className="display text-2xl md:text-4xl font-light text-muted mb-8">
              THE RISK IS <span className="text-paper font-bold">RANKED.</span>
            </p>

            {/* Description */}
            <p className="font-mono text-sm text-muted max-w-2xl mx-auto leading-relaxed mb-12">
              AERIS continuously collects environmental data from distributed IoT
              sensor nodes, processes the readings into engineered features,
              detects hazardous conditions with a hybrid rule engine + ML
              pipeline, and pushes live alerts before a fire, explosion, or
              toxic-gas incident escalates.
            </p>

            {/* CTA Buttons */}
            <div className="flex flex-col sm:flex-row items-center justify-center gap-4">
              <button
                onClick={() => router.push("/login")}
                className="group relative bg-volt text-ink font-mono text-xs uppercase tracking-wider px-8 py-4 hover:bg-volt/90 transition-all duration-300"
              >
                <span className="relative z-10">ACCESS DASHBOARD</span>
                <div className="absolute inset-0 bg-volt/20 scale-0 group-hover:scale-100 transition-transform duration-300" />
              </button>
              <a
                href="#features"
                className="font-mono text-xs text-muted hover:text-paper border border-line hover:border-paper/30 px-8 py-4 transition-colors"
              >
                EXPLORE FEATURES
              </a>
            </div>

            {/* Live indicator */}
            <div className="mt-16 flex items-center justify-center gap-2">
              <span className="inline-block w-2 h-2 bg-jade rounded-full blink" />
              <span className="font-mono text-[10px] text-jade tracking-wider">
                MONITORING ACTIVE
              </span>
            </div>
          </div>

          {/* Scroll indicator */}
          <div className="absolute bottom-8 left-1/2 -translate-x-1/2">
            <div className="font-mono text-[10px] text-faint tracking-wider animate-bounce">
              SCROLL
            </div>
          </div>
        </section>

        {/* Stats Section */}
        <section className="py-20 border-y border-line">
          <div className="max-w-6xl mx-auto px-6">
            <div className="grid grid-cols-2 md:grid-cols-4 gap-8">
              <StatBlock value="6" label="SENSOR TYPES" />
              <StatBlock value="5" label="HAZARD CLASSES" />
              <StatBlock value="<100ms" label="DETECTION LATENCY" />
              <StatBlock value="99.7%" label="ENSEMBLE F1" />
            </div>
          </div>
        </section>

        {/* Features Section */}
        <section id="features" className="py-24">
          <div className="max-w-6xl mx-auto px-6">
            <div className="mb-16">
              <div className="font-mono text-micro text-muted tracking-[0.3em] mb-4">
                CAPABILITIES
              </div>
              <h2 className="display text-4xl md:text-5xl font-bold">
                <span className="text-paper">BUILT FOR </span>
                <span className="text-volt">SAFETY.</span>
              </h2>
            </div>

            <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-6">
              <FeatureCard
                index="01"
                title="IoT Sensor Network"
                description="Distributed ESP32 nodes with MQ-2/MQ-4 gas sensors, DHT22 temperature/humidity, and flame detection. 2-second publish cadence via MQTT."
                icon={
                  <svg className="w-6 h-6" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
                    <path d="M8.288 15.038a5.25 5.25 0 017.424 0M5.106 11.856c3.807-3.808 9.98-3.808 13.788 0M1.924 8.674c5.565-5.565 14.587-5.565 20.152 0M12.53 18.22l-.53.53-.53-.53a.75.75 0 011.06 0z" />
                  </svg>
                }
              />
              <FeatureCard
                index="02"
                title="Hybrid Risk Engine"
                description="Three-level evaluation: hard safety rules, trend analysis with rate-of-change, and ML pattern recognition. Rules always override ML for known dangers."
                icon={
                  <svg className="w-6 h-6" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
                    <path d="M3.75 13.5l10.5-11.25L12 10.5h8.25L9.75 21.75 12 13.5H3.75z" />
                  </svg>
                }
              />
              <FeatureCard
                index="03"
                title="ML Ensemble"
                description="XGBoost, Random Forest, Gradient Boosting, Isolation Forest, and LSTM. Weighted soft-voting ensemble with 99.7% macro-F1 on hazard classification."
                icon={
                  <svg className="w-6 h-6" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
                    <path d="M9.75 3.104v5.714a2.25 2.25 0 01-.659 1.591L5 14.5M9.75 3.104c-.251.023-.501.05-.75.082m.75-.082a24.301 24.301 0 014.5 0m0 0v5.714c0 .597.237 1.17.659 1.591L19.8 15.3M14.25 3.104c.251.023.501.05.75.082M19.8 15.3l-1.57.393A9.065 9.065 0 0112 15a9.065 9.065 0 00-6.23.693L5 14.5m14.8.8l1.402 1.402c1.232 1.232.65 3.318-1.067 3.611A48.309 48.309 0 0112 21c-2.773 0-5.491-.235-8.135-.687-1.718-.293-2.3-2.379-1.067-3.61L5 14.5" />
                  </svg>
                }
              />
              <FeatureCard
                index="04"
                title="10-Minute Forecast"
                description="Risk projection regressor predicts the worst rule-engine score inside the next 10 minutes. Beat-the-baseline validated with episode-level holdout."
                icon={
                  <svg className="w-6 h-6" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
                    <path d="M12 6v6h4.5m4.5 0a9 9 0 11-18 0 9 9 0 0118 0z" />
                  </svg>
                }
              />
              <FeatureCard
                index="05"
                title="GIS Dispersion Map"
                description="Gaussian plume atmospheric dispersion model with Pasquill-Gifford coefficients. KML export for Google Earth visualization of toxic gas spread."
                icon={
                  <svg className="w-6 h-6" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
                    <path d="M9 6.75V15m6-6v8.25m.503-11.334a24.266 24.266 0 014.166.097M7.5 3H18A2.25 2.25 0 0120.25 5.25v9.5A2.25 2.25 0 0118 17H7.5A2.25 2.25 0 015.25 14.75v-9.5A2.25 2.25 0 017.5 3z" />
                  </svg>
                }
              />
              <FeatureCard
                index="06"
                title="Real-time Alerts"
                description="WebSocket push to dashboard, Redis fan-out bus, email and SMS providers. Alert acknowledgment with audit trail for safety officers."
                icon={
                  <svg className="w-6 h-6" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
                    <path d="M14.857 17.082a23.848 23.848 0 005.454-1.31A8.967 8.967 0 0118 9.75v-.7V9A6 6 0 006 9v.75a8.967 8.967 0 01-2.312 6.022c1.733.64 3.56 1.085 5.455 1.31m5.714 0a24.255 24.255 0 01-5.714 0m5.714 0a3 3 0 11-5.714 0" />
                  </svg>
                }
              />
            </div>
          </div>
        </section>

        {/* Architecture Section */}
        <section id="architecture" className="py-24 border-t border-line">
          <div className="max-w-6xl mx-auto px-6">
            <div className="mb-16">
              <div className="font-mono text-micro text-muted tracking-[0.3em] mb-4">
                ARCHITECTURE
              </div>
              <h2 className="display text-4xl md:text-5xl font-bold">
                <span className="text-paper">FROM SENSOR </span>
                <span className="text-volt">TO ALERT.</span>
              </h2>
            </div>

            {/* Pipeline visualization */}
            <div className="relative">
              {/* Connection line */}
              <div className="hidden md:block absolute top-1/2 left-0 right-0 h-px bg-line" />

              <div className="grid md:grid-cols-5 gap-4 relative">
                {[
                  { step: "01", label: "SENSORS", sub: "ESP32 + MQ/DHT" },
                  { step: "02", label: "MQTT", sub: "Mosquitto Broker" },
                  { step: "03", label: "PROCESS", sub: "Features + Rules" },
                  { step: "04", label: "ML", sub: "Ensemble + Forecast" },
                  { step: "05", label: "ALERT", sub: "Dashboard + SMS" },
                ].map((item, i) => (
                  <div key={i} className="relative text-center">
                    <div className="bg-ink border border-line p-4 relative z-10">
                      <div className="font-mono text-micro text-volt tracking-[0.2em] mb-2">
                        {item.step}
                      </div>
                      <div className="text-type text-sm uppercase tracking-wider">
                        {item.label}
                      </div>
                      <div className="font-mono text-[10px] text-muted mt-1">
                        {item.sub}
                      </div>
                    </div>
                    {i < 4 && (
                      <div className="hidden md:block absolute top-1/2 -right-2 w-4 h-px bg-volt z-20" />
                    )}
                  </div>
                ))}
              </div>
            </div>
          </div>
        </section>

        {/* CTA Section */}
        <section className="py-24 border-t border-line">
          <div className="max-w-4xl mx-auto px-6 text-center">
            <div className="font-mono text-micro text-volt tracking-[0.3em] mb-6">
              READY TO MONITOR
            </div>
            <h2 className="display text-4xl md:text-6xl font-bold mb-8">
              <span className="text-paper">PROTECT YOUR </span>
              <span className="text-volt">WORKFORCE.</span>
            </h2>
            <p className="font-mono text-sm text-muted max-w-xl mx-auto mb-12">
              Access the real-time monitoring dashboard to visualize sensor
              data, track risk levels, and receive instant alerts before
              hazardous conditions escalate.
            </p>
            <button
              onClick={() => router.push("/login")}
              className="group relative bg-volt text-ink font-mono text-xs uppercase tracking-wider px-12 py-5 hover:bg-volt/90 transition-all duration-300"
            >
              <span className="relative z-10">ENTER AERIS</span>
              <div className="absolute inset-0 bg-volt/20 scale-0 group-hover:scale-100 transition-transform duration-300" />
            </button>
          </div>
        </section>

        {/* Footer */}
        <footer className="py-8 border-t border-line">
          <div className="max-w-6xl mx-auto px-6 flex flex-col md:flex-row items-center justify-between gap-4">
            <div className="font-mono text-[10px] text-faint tracking-wider">
              AERIS v0.1.0 — PROTOTYPE DECISION-SUPPORT SYSTEM
            </div>
            <div className="font-mono text-[10px] text-faint tracking-wider">
              NOT A SUBSTITUTE FOR CERTIFIED FIRE & GAS DETECTION EQUIPMENT
            </div>
          </div>
        </footer>
      </div>

      {/* Scanline animation keyframes */}
      <style jsx>{`
        @keyframes scanline {
          0% { top: -10%; }
          100% { top: 110%; }
        }
      `}</style>
    </div>
  );
}
