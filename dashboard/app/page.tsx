import Link from "next/link";
import { Activity, CheckCircle2, Coins, AlertCircle, Clock, Mic, Image as ImageIcon, FileText, ChevronRight } from "lucide-react";

import { fetchMetrics, fetchRuns } from "@/lib/api";
import type { Run, RunStatus } from "@/lib/types";

export const dynamic = "force-dynamic";
export const revalidate = 0;

const STATUS_LABEL: Record<RunStatus, string> = {
  queued: "En cola",
  processing: "Procesando",
  completed: "OK",
  failed: "Falló",
  skipped: "Skip",
};

function formatDateTime(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleString("es-AR", {
      day: "2-digit", month: "2-digit", year: "2-digit",
      hour: "2-digit", minute: "2-digit", second: "2-digit",
    });
  } catch {
    return iso;
  }
}

function formatUsd(v: number | null): string {
  if (v == null) return "—";
  return `$${v.toFixed(6)}`;
}

function formatSeconds(v: number | null): string {
  if (v == null) return "—";
  return `${v.toFixed(1)}s`;
}

function StatusBadge({ status }: { status: RunStatus }) {
  return <span className={`badge badge-${status}`}>{STATUS_LABEL[status]}</span>;
}

function KindBadge({ kind }: { kind: string | null }) {
  if (!kind) return <span className="text-slate-500">—</span>;
  const Icon = kind === "audio" ? Mic : kind === "image" ? ImageIcon : FileText;
  const label = kind === "audio" ? "Audio" : kind === "image" ? "Imagen" : "Documento";
  return (
    <span className="inline-flex items-center gap-1.5 text-slate-300">
      <Icon className="h-3.5 w-3.5 text-slate-400" />
      {label}
    </span>
  );
}

function KpiCard({ icon: Icon, label, value, sub }: {
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  value: string;
  sub?: string;
}) {
  return (
    <div className="glass-panel rounded-xl p-5">
      <div className="flex items-center gap-3">
        <Icon className="h-5 w-5 text-blue-400" />
        <div className="text-xs uppercase tracking-wide text-slate-400">{label}</div>
      </div>
      <div className="mt-3 text-3xl font-semibold text-white">{value}</div>
      {sub && <div className="mt-1 text-xs text-slate-500">{sub}</div>}
    </div>
  );
}

export default async function DashboardPage() {
  let metrics;
  let runsPage;
  let error: string | null = null;

  try {
    [metrics, runsPage] = await Promise.all([
      fetchMetrics(),
      fetchRuns({ limit: 50 }),
    ]);
  } catch (e) {
    error = e instanceof Error ? e.message : String(e);
  }

  return (
    <main className="min-h-screen px-6 py-8 md:px-12">
      <header className="mb-8 flex items-center justify-between">
        <div>
          <h1 className="flex items-center gap-3 text-2xl font-semibold">
            <Mic className="h-6 w-6 text-blue-400" />
            MISS — Dashboard
          </h1>
          <p className="mt-1 text-sm text-slate-400">
            Transcripciones y descripciones de Spoter, runs y costos.
          </p>
        </div>
      </header>

      {error && (
        <div className="glass-panel mb-6 rounded-xl border-red-500/40 p-5 text-red-300">
          <div className="flex items-center gap-2 font-medium">
            <AlertCircle className="h-4 w-4" />
            No pude leer datos de MISS
          </div>
          <div className="mt-2 text-sm font-mono">{error}</div>
        </div>
      )}

      {metrics && (
        <section className="mb-8 grid grid-cols-2 gap-4 md:grid-cols-4">
          <KpiCard icon={Activity} label="Runs totales" value={String(metrics.total)} />
          <KpiCard
            icon={CheckCircle2}
            label="Completados"
            value={String(metrics.completed)}
            sub={metrics.failed ? `${metrics.failed} fallos` : "sin fallos"}
          />
          <KpiCard
            icon={Coins}
            label="Costo total"
            value={`$${metrics.total_cost_usd.toFixed(4)}`}
            sub="USD"
          />
          <KpiCard
            icon={Clock}
            label="Audio transcripto"
            value={`${(metrics.total_duration_seconds / 60).toFixed(1)}m`}
            sub={`${metrics.total_duration_seconds.toFixed(0)}s totales`}
          />
        </section>
      )}

      <section className="glass-panel rounded-xl">
        <div className="border-b border-white/5 px-5 py-4 text-sm font-medium text-slate-300">
          Últimas runs
        </div>
        {runsPage && runsPage.items.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-xs uppercase text-slate-500">
                <tr>
                  <th className="px-5 py-3 text-left">Fecha</th>
                  <th className="px-5 py-3 text-left">Tipo</th>
                  <th className="px-5 py-3 text-left">Instance</th>
                  <th className="px-5 py-3 text-left">Teléfono</th>
                  <th className="px-5 py-3 text-left">Estado</th>
                  <th className="px-5 py-3 text-left">Contacto</th>
                  <th className="px-5 py-3 text-right">Duración</th>
                  <th className="px-5 py-3 text-right">Costo</th>
                  <th className="px-5 py-3"></th>
                </tr>
              </thead>
              <tbody>
                {runsPage.items.map((r: Run) => (
                  <tr
                    key={r.id}
                    className="group cursor-pointer border-t border-white/5 transition hover:bg-white/5"
                  >
                    <td className="px-5 py-3 text-slate-300 whitespace-nowrap">
                      <Link href={`/runs/${r.id}`} className="hover:text-blue-400">
                        {formatDateTime(r.created_at)}
                      </Link>
                    </td>
                    <td className="px-5 py-3 text-sm">
                      <KindBadge kind={r.attachment_kind} />
                    </td>
                    <td className="px-5 py-3 text-slate-400 font-mono">
                      {r.instance_root}
                      {r.sub_instance && r.sub_instance !== r.instance_root
                        ? ` / ${r.sub_instance}`
                        : ""}
                    </td>
                    <td className="px-5 py-3 text-slate-300 font-mono">{r.phone || "—"}</td>
                    <td className="px-5 py-3">
                      <StatusBadge status={r.status} />
                    </td>
                    <td className="px-5 py-3 text-slate-300">{r.contact_name || "—"}</td>
                    <td className="px-5 py-3 text-slate-400 text-right">
                      {formatSeconds(r.transcription_duration_seconds)}
                    </td>
                    <td className="px-5 py-3 text-slate-400 text-right font-mono">
                      {formatUsd(r.transcription_cost_usd)}
                    </td>
                    <td className="px-5 py-3 text-right">
                      <Link
                        href={`/runs/${r.id}`}
                        className="inline-flex items-center gap-1 text-xs text-slate-500 hover:text-blue-400"
                      >
                        Ver
                        <ChevronRight className="h-3.5 w-3.5" />
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="px-5 py-8 text-center text-sm text-slate-500">
            {error ? "—" : "Todavía no hay runs. Cuando Spoter mande el primer webhook aparecerá acá."}
          </div>
        )}
      </section>
    </main>
  );
}
