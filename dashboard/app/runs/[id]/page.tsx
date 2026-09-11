import Link from "next/link";
import { ChevronLeft, AlertCircle } from "lucide-react";
import { notFound } from "next/navigation";

import { fetchRun } from "@/lib/api";
import type { OutboundCall, RunStatus } from "@/lib/types";

export const dynamic = "force-dynamic";
export const revalidate = 0;

const STATUS_LABEL: Record<RunStatus, string> = {
  queued: "En cola",
  processing: "Procesando",
  completed: "OK",
  failed: "Falló",
  skipped: "Skip",
};

function Row({ label, value, mono = false }: { label: string; value: React.ReactNode; mono?: boolean }) {
  return (
    <div className="grid grid-cols-1 gap-1 border-t border-white/5 px-5 py-3 md:grid-cols-3 md:gap-4">
      <div className="text-xs uppercase tracking-wide text-slate-500">{label}</div>
      <div className={`md:col-span-2 ${mono ? "font-mono text-sm" : "text-sm"} text-slate-200`}>
        {value ?? "—"}
      </div>
    </div>
  );
}

export default async function RunDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const run = await fetchRun(id);

  if (!run) {
    notFound();
  }

  return (
    <main className="min-h-screen px-6 py-8 md:px-12">
      <Link
        href="/"
        className="mb-6 inline-flex items-center gap-1.5 text-sm text-slate-400 hover:text-slate-200"
      >
        <ChevronLeft className="h-4 w-4" />
        Volver al dashboard
      </Link>

      <div className="glass-panel rounded-xl">
        <div className="border-b border-white/5 px-5 py-4">
          <div className="flex items-center gap-3">
            <span className={`badge badge-${run.status}`}>{STATUS_LABEL[run.status]}</span>
            <span className="font-mono text-xs text-slate-400">{run.id}</span>
          </div>
          {run.error_message && (
            <div className="mt-3 flex items-start gap-2 rounded-lg bg-red-500/10 p-3 text-sm text-red-300">
              <AlertCircle className="h-4 w-4 flex-shrink-0" />
              <span className="font-mono">{run.error_message}</span>
            </div>
          )}
        </div>

        <Row label="Creado" value={run.created_at} mono />
        <Row label="Actualizado" value={run.updated_at} mono />
        <Row label="Event type" value={run.event_type} />
        <Row label="Instance root / sub" value={`${run.instance_root} / ${run.sub_instance || "—"}`} mono />
        <Row label="Teléfono" value={run.phone} mono />
        <Row label="Adjunto" value={run.attachment_kind || "—"} />
        <Row
          label="Media URL"
          value={
            run.attachment_url ? (
              <a
                href={run.attachment_url}
                target="_blank"
                rel="noreferrer"
                className="break-all text-blue-400 hover:text-blue-300"
              >
                {run.attachment_url}
              </a>
            ) : (
              "—"
            )
          }
          mono
        />
        <Row label="Contacto resuelto" value={run.contact_name || "—"} />
        <Row
          label="Duración audio"
          value={run.transcription_duration_seconds != null ? `${run.transcription_duration_seconds.toFixed(2)} s` : null}
        />
        <Row
          label="Costo"
          value={run.transcription_cost_usd != null ? `$${run.transcription_cost_usd.toFixed(6)} USD` : null}
        />
        <Row label="MASS id_original" value={run.mass_id_original} mono />

        {run.transcription_text && (
          <div className="border-t border-white/5 px-5 py-4">
            <div className="mb-2 text-xs uppercase tracking-wide text-slate-500">Texto de la nota</div>
            <div className="whitespace-pre-wrap rounded-lg bg-slate-950/50 p-4 text-sm text-slate-100">
              {run.transcription_text}
            </div>
          </div>
        )}

        {run.raw_payload && (
          <div className="border-t border-white/5 px-5 py-4">
            <div className="mb-2 text-xs uppercase tracking-wide text-slate-500">
              Payload recibido
            </div>
            <pre className="overflow-x-auto rounded-lg bg-slate-950/50 p-4 text-xs text-slate-200">
              {prettyJson(run.raw_payload)}
            </pre>
          </div>
        )}

        {run.outbound_calls && (
          <div className="border-t border-white/5 px-5 py-4">
            <div className="mb-2 text-xs uppercase tracking-wide text-slate-500">
              Llamadas salientes
            </div>
            <OutboundCallsTable json={run.outbound_calls} />
          </div>
        )}
      </div>
    </main>
  );
}

function prettyJson(raw: string): string {
  try {
    return JSON.stringify(JSON.parse(raw), null, 2);
  } catch {
    return raw;
  }
}

function OutboundCallsTable({ json }: { json: string }) {
  let calls: OutboundCall[] = [];
  try {
    calls = JSON.parse(json) as OutboundCall[];
  } catch {
    return <div className="text-sm text-red-300">JSON inválido</div>;
  }
  if (calls.length === 0) {
    return <div className="text-sm text-slate-500">Sin llamadas salientes.</div>;
  }
  return (
    <div className="space-y-3">
      {calls.map((c, i) => (
        <details key={i} className="rounded-lg bg-slate-950/50 open:pb-3">
          <summary className="cursor-pointer list-none px-3 py-2 text-xs">
            <div className="flex items-center gap-3">
              <span className="text-slate-500 whitespace-nowrap">
                {new Date(c.ts).toLocaleTimeString("es-AR")}
              </span>
              <span className="font-mono text-slate-200">{c.method}</span>
              <span
                className={
                  c.status >= 400
                    ? "font-mono text-red-300"
                    : "font-mono text-emerald-300"
                }
              >
                {c.status}
              </span>
              <span className="font-mono text-slate-500">{c.ms ?? "—"}ms</span>
              <span className="flex-1 truncate font-mono text-slate-300">
                {c.url}
              </span>
            </div>
          </summary>
          {c.request_headers && Object.keys(c.request_headers).length > 0 && (
            <div className="mx-3 mt-2 rounded bg-black/40 p-2 text-xs">
              <div className="mb-1 text-slate-500">Request headers</div>
              <pre className="whitespace-pre-wrap font-mono text-slate-300">
                {Object.entries(c.request_headers)
                  .map(([k, v]) => `${k}: ${v}`)
                  .join("\n")}
              </pre>
            </div>
          )}
          {c.request_body && (
            <div className="mx-3 mt-2 rounded bg-black/40 p-2 text-xs">
              <div className="mb-1 text-slate-500">Request body</div>
              <pre className="whitespace-pre-wrap font-mono text-slate-100">
                {prettyJson(c.request_body)}
              </pre>
            </div>
          )}
        </details>
      ))}
    </div>
  );
}
