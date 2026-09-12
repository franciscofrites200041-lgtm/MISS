"use client";

import { useEffect, useMemo, useState } from "react";
import { Play, Loader2, AlertCircle, FileUp } from "lucide-react";
import { fetchModels, testTool } from "@/lib/api";
import type { LlmModel, ToolTestResult } from "@/lib/types";

const ACCEPT: Record<string, string> = {
  audio: "audio/*",
  image: "image/*",
  document: "application/pdf",
};

const FILE_HINT: Record<string, string> = {
  audio: "mp3, wav, ogg, m4a…",
  image: "png, jpg, webp, gif…",
  document: "solo PDF",
};

function formatBytes(n: number): string {
  if (n >= 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  if (n >= 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${n} B`;
}

export default function Tester({
  slug,
  kind,
  currentModel,
}: {
  slug: string;
  kind: string;
  currentModel: string;
}) {
  const [models, setModels] = useState<LlmModel[]>([]);
  const [file, setFile] = useState<File | null>(null);
  const [modelChoice, setModelChoice] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<ToolTestResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchModels(kind)
      .then((data) => {
        if (!cancelled) setModels(data.models);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [kind]);

  const selectedLabel = useMemo(() => {
    if (!modelChoice) return `Modelo configurado (${currentModel})`;
    const found = models.find((m) => m.id === modelChoice);
    return found ? found.name : modelChoice;
  }, [modelChoice, models, currentModel]);

  const run = async () => {
    if (!file) {
      setError("Elegí un archivo primero.");
      setResult(null);
      return;
    }
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const formData = new FormData();
      formData.append("file", file);
      formData.append("model", modelChoice);
      setResult(await testTool(slug, formData));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="glass-panel rounded-xl">
      <div className="flex items-center gap-2 border-b border-white/5 px-5 py-4">
        <FileUp className="h-4 w-4 text-blue-300" />
        <h2 className="text-lg font-semibold">Probar herramienta</h2>
        <span className="ml-auto text-xs text-slate-500">
          Subís un archivo y se procesa con el modelo elegido. No se guarda.
        </span>
      </div>

      <div className="space-y-4 px-5 py-5">
        <div className="grid gap-4 md:grid-cols-2">
          <div>
            <label className="mb-1.5 block text-xs uppercase tracking-wide text-slate-500">
              Archivo ({FILE_HINT[kind]})
            </label>
            <input
              type="file"
              accept={ACCEPT[kind]}
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              className="w-full rounded-lg border border-white/5 bg-black/30 px-3 py-2 text-sm text-slate-100 focus:border-blue-500/50 focus:outline-none file:mr-3 file:rounded file:border-0 file:bg-blue-500/20 file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-blue-200"
            />
          </div>

          <div>
            <label className="mb-1.5 block text-xs uppercase tracking-wide text-slate-500">
              Modelo de prueba
            </label>
            <select
              value={modelChoice}
              onChange={(e) => setModelChoice(e.target.value)}
              className="w-full rounded-lg border border-white/5 bg-black/30 px-3 py-2 text-sm text-slate-100 focus:border-blue-500/50 focus:outline-none"
            >
              <option value="">
                Modelo configurado ({currentModel})
              </option>
              {models.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.name}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={run}
            disabled={loading}
            className="flex items-center gap-2 rounded-lg bg-blue-500/20 px-4 py-2 text-sm text-blue-200 hover:bg-blue-500/30 disabled:opacity-50"
          >
            {loading ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Play className="h-4 w-4" />
            )}
            Probar
          </button>
          {loading && (
            <span className="text-xs text-slate-400">
              Procesando con {selectedLabel}…
            </span>
          )}
        </div>

        {error && (
          <div className="flex items-start gap-2 rounded-lg bg-red-500/10 px-4 py-3 text-sm text-red-300">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        {result && (
          <div className="space-y-4">
            <dl className="grid grid-cols-2 gap-3 rounded-lg border border-white/5 bg-black/20 p-4 text-sm md:grid-cols-4">
              <div>
                <dt className="text-xs text-slate-500">Modelo</dt>
                <dd className="mt-0.5 break-all font-mono">{result.model}</dd>
              </div>
              <div>
                <dt className="text-xs text-slate-500">Costo</dt>
                <dd className="mt-0.5 font-mono">
                  {result.cost_usd != null
                    ? `$${result.cost_usd.toFixed(6)}`
                    : "—"}
                </dd>
              </div>
              <div>
                <dt className="text-xs text-slate-500">Latencia</dt>
                <dd className="mt-0.5 font-mono">
                  {result.latency_ms != null
                    ? `${result.latency_ms} ms`
                    : "—"}
                </dd>
              </div>
              <div>
                <dt className="text-xs text-slate-500">Duración (audio)</dt>
                <dd className="mt-0.5 font-mono">
                  {result.duration_seconds != null
                    ? `${result.duration_seconds.toFixed(1)} s`
                    : "—"}
                </dd>
              </div>
            </dl>

            <div>
              <div className="mb-1.5 flex items-center justify-between text-xs text-slate-500">
                <span>Resultado</span>
                <span className="font-mono">
                  {result.file.name} · {formatBytes(result.file.size)}
                </span>
              </div>
              <pre className="whitespace-pre-wrap break-words rounded-lg border border-white/5 bg-black/30 px-4 py-3 font-mono text-sm text-slate-100">
                {result.text}
              </pre>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}