import Link from "next/link";
import { ChevronLeft } from "lucide-react";
import { notFound } from "next/navigation";

import { fetchTool } from "@/lib/api";
import type { Run, RunStatus } from "@/lib/types";
import ToolEditor from "./editor";
import Tester from "./tester";

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
    return new Date(iso).toLocaleString("es-AR", {
      day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

export default async function ToolDetailPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  const tool = await fetchTool(slug);
  if (!tool) notFound();

  return (
    <main className="min-h-screen px-6 py-8 md:px-12">
      <Link
        href="/tools"
        className="mb-6 inline-flex items-center gap-1.5 text-sm text-slate-400 hover:text-slate-200"
      >
        <ChevronLeft className="h-4 w-4" />
        Volver a Herramientas
      </Link>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_360px]">
        <ToolEditor tool={tool} />

        <aside className="space-y-6">
          <div className="glass-panel rounded-xl p-5">
            <h3 className="text-sm font-medium text-slate-300">Uso</h3>
            <dl className="mt-3 grid grid-cols-2 gap-3 text-sm">
              <div>
                <dt className="text-xs text-slate-500">Total</dt>
                <dd className="font-mono">{tool.stats.total}</dd>
              </div>
              <div>
                <dt className="text-xs text-slate-500">OK / Fallos</dt>
                <dd className="font-mono">
                  {tool.stats.completed} / {tool.stats.failed}
                </dd>
              </div>
              <div>
                <dt className="text-xs text-slate-500">Skips</dt>
                <dd className="font-mono">{tool.stats.skipped}</dd>
              </div>
              <div>
                <dt className="text-xs text-slate-500">Costo total</dt>
                <dd className="font-mono">${tool.stats.total_cost_usd.toFixed(4)}</dd>
              </div>
              {tool.kind === "audio" && (
                <div className="col-span-2">
                  <dt className="text-xs text-slate-500">Audio transcripto</dt>
                  <dd className="font-mono">
                    {(tool.stats.total_duration_seconds / 60).toFixed(1)} min
                  </dd>
                </div>
              )}
            </dl>
          </div>

          <div className="glass-panel rounded-xl">
            <div className="border-b border-white/5 px-5 py-3 text-sm font-medium text-slate-300">
              Últimas runs
            </div>
            {tool.recent_runs.length === 0 ? (
              <div className="px-5 py-6 text-center text-sm text-slate-500">
                Sin runs todavía.
              </div>
            ) : (
              <ul className="divide-y divide-white/5">
                {tool.recent_runs.map((r: Run) => (
                  <li key={r.id} className="px-5 py-3 text-sm">
                    <Link
                      href={`/runs/${r.id}`}
                      className="flex items-center justify-between gap-3 hover:text-blue-400"
                    >
                      <span className="text-slate-400">
                        {formatDateTime(r.created_at)}
                      </span>
                      <span className={`badge badge-${r.status}`}>
                        {STATUS_LABEL[r.status]}
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </aside>
      </div>

      <div className="mt-6">
        <Tester slug={tool.slug} kind={tool.kind} currentModel={tool.model} />
      </div>
    </main>
  );
}

