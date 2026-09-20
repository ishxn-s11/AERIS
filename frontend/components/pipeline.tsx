"use client";
/** Numbered pipeline strip: SENSE → CONNECT → PROCESS → PREDICT → ALERT. */

const STEPS = [
  { n: "01", label: "Sense", note: "MQ gas · DHT22 · flame" },
  { n: "02", label: "Connect", note: "ESP32 → MQTT QoS 1" },
  { n: "03", label: "Process", note: "validate · filter · features" },
  { n: "04", label: "Predict", note: "rules + random forest" },
  { n: "05", label: "Alert", note: "zone · severity · officer" },
];

export function PipelineStrip({ className = "" }: { className?: string }) {
  return (
    <div className={`grid grid-cols-2 gap-px border-t border-line md:grid-cols-5 ${className}`}>
      {STEPS.map((s) => (
        <div key={s.n} className="border-b border-line py-4 pr-4">
          <div className="micro text-volt">{s.n}</div>
          <div className="mt-2 text-sm font-semibold uppercase tracking-[0.12em] text-paper">
            {s.label}
          </div>
          <div className="micro mt-1.5 text-faint">{s.note}</div>
        </div>
      ))}
    </div>
  );
}
