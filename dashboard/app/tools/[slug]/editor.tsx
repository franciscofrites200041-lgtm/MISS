"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Save, Power, Loader2, Check, AlertCircle } from "lucide-react";
import type { LlmModel, LlmModelsResponse, ToolDetail } from "@/lib/types";

export default function ToolEditor({ tool }: { tool: ToolDetail }) {
  const router = useRouter();
  const [enabled, setEnabled] = useState(tool.enabled);
  const [model, setModel] = useState(tool.model);
  const [notePrefix, setNotePrefix] = useState(tool.note_prefix);
  const [prompt, setPrompt] = useState(tool.prompt ?? "");
  const [saving, setSaving] = useState(false);
  const [feedback, setFeedback] = useState<
    { kind: "ok" | "error"; msg: string } | null
  >(null);

  const [models, setModels] = useState<LlmModel[] | null>(null);
  const [modelsLoading, setModelsLoading] = useState(true);
  const [modelsError, setModelsError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch(`/api/models?kind=${encodeURIComponent(tool.kind)}`)
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then((data: LlmModelsResponse) => {
        if (cancelled) return;
        const fallback: LlmModel = { id: tool.model, name: tool.model };
        const hasCurrent = data.models.some((m) => m.id === tool.model);
        setModels(hasCurrent ? data.models : [fallback, ...data.models]);
      })
      .catch(() => {
        if (cancelled) return;
        setModels([{ id: tool.model, name: tool.model }]);
        setModelsError("No se pudo cargar el catálogo de OpenRouter.");
      })
      .finally(() => {
        if (!cancelled) setModelsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [tool.kind, tool.model]);

  const patch = async (fields: Record<string, unknown>) => {
    setSaving(true);
    setFeedback(null);
    try {
      const res = await fetch(`/api/tools/${encodeURIComponent(tool.slug)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(fields),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setFeedback({ kind: "ok", msg: "Guardado" });
      router.refresh();
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setFeedback({ kind: "error", msg });
    } finally {
      setSaving(false);
    }
  };

  const toggleEnabled = async () => {
    const next = !enabled;
    setEnabled(next);
    await patch({ enabled: next });
  };

  const saveAll = async () => {
    await patch({
      model,
      note_prefix: notePrefix,
      prompt: tool.kind === "audio" ? null : prompt,
    });
  };

  return (
    <div className="glass-panel rounded-xl">
      <div className="flex items-start justify-between gap-3 border-b border-white/5 px-5 py-4">
        <div>
          <h1 className="text-xl font-semibold">{tool.name}</h1>
          <p className="mt-1 text-sm text-slate-400">{tool.description}</p>
          <div className="mt-2 flex items-center gap-2 text-xs text-slate-500">
            <span className="rounded bg-slate-800/60 px-2 py-0.5 font-mono">
              {tool.kind}
            </span>
            <span className="font-mono">/{tool.slug}</span>
          </div>
        </div>
        <button
          onClick={toggleEnabled}
          disabled={saving}
          className={
            enabled
              ? "flex items-center gap-2 rounded-lg bg-emerald-500/10 px-3 py-1.5 text-sm text-emerald-300 hover:bg-emerald-500/20"
              : "flex items-center gap-2 rounded-lg bg-slate-500/10 px-3 py-1.5 text-sm text-slate-400 hover:bg-slate-500/20"
          }
        >
          <Power className="h-4 w-4" />
          {enabled ? "Habilitada" : "Deshabilitada"}
        </button>
      </div>

      <div className="space-y-5 px-5 py-5">
        <div>
          <label className="mb-1.5 block text-xs uppercase tracking-wide text-slate-500">
            Modelo de OpenRouter
          </label>
          <select
            value={model}
            onChange={(e) => setModel(e.target.value)}
            disabled={modelsLoading}
            className="w-full rounded-lg border border-white/5 bg-black/30 px-3 py-2 text-sm text-slate-100 focus:border-blue-500/50 focus:outline-none disabled:opacity-50"
          >
            {modelsLoading ? (
              <option value={model}>{model}</option>
            ) : (
              (models ?? []).map((m) => (
                <option key={m.id} value={m.id}>
                  {m.name}
                </option>
              ))
            )}
          </select>
          {modelsError ? (
            <p className="mt-1 flex items-center gap-1 text-xs text-amber-300">
              <AlertCircle className="h-3.5 w-3.5" />
              {modelsError} Se mantiene el modelo actual configurado.
            </p>
          ) : (
            <p className="mt-1 text-xs text-slate-500">
              Modelos disponibles en OpenRouter para tareas de tipo{" "}
              <span className="font-mono">{tool.kind}</span>. El catálogo se
              refresca cada pocos minutos.
            </p>
          )}
        </div>

        <div>
          <label className="mb-1.5 block text-xs uppercase tracking-wide text-slate-500">
            Prefijo de la nota
          </label>
          <input
            type="text"
            value={notePrefix}
            onChange={(e) => setNotePrefix(e.target.value)}
            className="w-full rounded-lg border border-white/5 bg-black/30 px-3 py-2 text-sm text-slate-100 focus:border-blue-500/50 focus:outline-none"
          />
        </div>

        {tool.kind !== "audio" && (
          <div>
            <label className="mb-1.5 block text-xs uppercase tracking-wide text-slate-500">
              Prompt
            </label>
            <textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              rows={8}
              className="w-full rounded-lg border border-white/5 bg-black/30 px-3 py-2 font-mono text-sm text-slate-100 focus:border-blue-500/50 focus:outline-none"
            />
            <p className="mt-1 text-xs text-slate-500">
              Se envía como system/user prompt al modelo. Para documentos, si
              hay texto extraíble por código se anexa como &quot;Documento:
              &lt;texto&gt;&quot;.
            </p>
          </div>
        )}

        <div className="flex items-center justify-between gap-3 border-t border-white/5 pt-4">
          <div className="text-xs text-slate-500">
            Última edición: {new Date(tool.updated_at).toLocaleString("es-AR")}
          </div>
          <div className="flex items-center gap-3">
            {feedback && (
              <span
                className={
                  feedback.kind === "ok"
                    ? "flex items-center gap-1 text-xs text-emerald-300"
                    : "flex items-center gap-1 text-xs text-red-300"
                }
              >
                {feedback.kind === "ok" ? (
                  <Check className="h-3.5 w-3.5" />
                ) : (
                  <AlertCircle className="h-3.5 w-3.5" />
                )}
                {feedback.msg}
              </span>
            )}
            <button
              onClick={saveAll}
              disabled={saving}
              className="flex items-center gap-2 rounded-lg bg-blue-500/20 px-4 py-2 text-sm text-blue-200 hover:bg-blue-500/30 disabled:opacity-50"
            >
              {saving ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Save className="h-4 w-4" />
              )}
              Guardar
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}