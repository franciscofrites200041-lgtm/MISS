import Link from "next/link";
import { Wrench, AlertCircle, Coins, Activity, CheckCircle2 } from "lucide-react";

import { fetchTools } from "@/lib/api";
import type { ToolWithStats } from "@/lib/types";

export const dynamic = "force-dynamic";
export const revalidate = 0;

function formatUsd(v: number): string {
  return `$${v.toFixed(4)}`;
}

function ToolCard({ tool }: { tool: ToolWithStats }) {
  const { stats } = tool;
  return (
    <Link
      href={`/tools/${tool.slug}`}
      className="glass-panel block rounded-xl p-5 transition hover:border-white/20"
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <h3 className="text-lg font-semibold text-white">{tool.name}</h3>
            <span
              className={
                tool.enabled
                  ? "rounded-md bg-emerald-500/10 px-2 py-0.5 text-xs text-emerald-300"
                  : "rounded-md bg-slate-500/10 px-2 py-0.5 text-xs text-slate-400"
              }
            >
              {tool.enabled ? "ON" : "OFF"}
            </span>
          </div>
          <p className="mt-1 text-sm text-slate-400">{tool.description}</p>
          <div className="mt-3 font-mono text-xs text-slate-500">
            {tool.model}
          </div>
        </div>
        <span className="rounded-md bg-slate-800/60 px-2 py-1 font-mono text-xs text-slate-300">
          {tool.kind}
        </span>
      </div>

      <div className="mt-5 grid grid-cols-3 gap-3 text-sm">
        <div className="rounded-lg bg-black/20 p-3">
          <div className="flex items-center gap-1.5 text-xs text-slate-500">
            <Activity className="h-3.5 w-3.5" />
            Runs
          </div>
          <div className="mt-1 text-lg font-semibold">{stats.total}</div>
        </div>
        <div className="rounded-lg bg-black/20 p-3">
          <div className="flex items-center gap-1.5 text-xs text-slate-500">
            <CheckCircle2 className="h-3.5 w-3.5" />
            OK
          </div>
          <div className="mt-1 text-lg font-semibold">{stats.completed}</div>
        </div>
        <div className="rounded-lg bg-black/20 p-3">
          <div className="flex items-center gap-1.5 text-xs text-slate-500">
            <Coins className="h-3.5 w-3.5" />
            Costo
          </div>
          <div className="mt-1 font-mono text-sm">{formatUsd(stats.total_cost_usd)}</div>
        </div>
      </div>
    </Link>
  );
}

export default async function ToolsPage() {
  let items: ToolWithStats[] = [];
  let error: string | null = null;

  try {
    const list = await fetchTools();
    items = list.items;
  } catch (e) {
    error = e instanceof Error ? e.message : String(e);
  }

  return (
    <main className="min-h-screen px-6 py-8 md:px-12">
      <header className="mb-8">
        <h1 className="flex items-center gap-3 text-2xl font-semibold">
          <Wrench className="h-6 w-6 text-blue-400" />
          Herramientas
        </h1>
        <p className="mt-1 text-sm text-slate-400">
          Cada herramienta procesa un tipo de contenido. Podés cambiar el modelo,
          el prompt y activar/desactivar cada una.
        </p>
      </header>

      {error && (
        <div className="glass-panel mb-6 rounded-xl border-red-500/40 p-5 text-red-300">
          <div className="flex items-center gap-2 font-medium">
            <AlertCircle className="h-4 w-4" />
            No pude leer datos de MISS
          </div>
          <div className="mt-2 font-mono text-sm">{error}</div>
        </div>
      )}

      <div className="grid gap-4 md:grid-cols-2">
        {items.map((t) => (
          <ToolCard key={t.slug} tool={t} />
        ))}
      </div>
    </main>
  );
}
