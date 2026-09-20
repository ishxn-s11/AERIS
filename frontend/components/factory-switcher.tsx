"use client";
/** Plant selector for the masthead.
 *
 * Rendered only when the deployment actually has more than one factory —
 * a switcher with a single option is noise in an emergency console. The
 * selected plant is what every page scopes its queries to.
 */
import { useFactory } from "@/lib/factory";

export function FactorySwitcher() {
  const { factories, factoryId, factory, select, loading } = useFactory();

  if (loading || factories.length < 2) {
    return factory ? (
      <div className="hidden text-right xl:block">
        <div className="micro text-faint">Plant</div>
        <div className="micro text-paper">{factory.name}</div>
      </div>
    ) : null;
  }

  return (
    <label className="hidden items-center gap-2 xl:flex">
      <span className="micro text-faint">Plant</span>
      <select
        value={factoryId ?? "all"}
        onChange={(e) => select(e.target.value === "all" ? null : Number(e.target.value))}
        className="micro cursor-pointer appearance-none border border-line bg-ink-2 px-2.5 py-2 text-paper outline-none transition-colors hover:border-muted focus:border-volt"
      >
        <option value="all">All plants · {factories.length}</option>
        {factories.map((f) => (
          <option key={f.id} value={f.id}>
            {f.name} · {f.location}
          </option>
        ))}
      </select>
    </label>
  );
}
