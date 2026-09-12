import type {
  LlmModel,
  LlmModelsResponse,
  MetricsSummary,
  Run,
  RunsPage,
  Tool,
  ToolDetail,
  ToolPatch,
  ToolTestResult,
  ToolsList,
} from "./types";

const API_URL = process.env.MISS_API_URL || "http://miss:8000";
const API_USER = process.env.MISS_API_USER || "";
const API_PASS = process.env.MISS_API_PASS || "";

function authHeader(): Record<string, string> {
  if (!API_USER || !API_PASS) return {};
  const token = Buffer.from(`${API_USER}:${API_PASS}`).toString("base64");
  return { Authorization: `Basic ${token}` };
}

async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: {
      ...authHeader(),
      ...(init?.body && typeof init.body === "string"
        ? { "Content-Type": "application/json" }
        : {}),
      ...(init?.headers || {}),
    },
    cache: "no-store",
  });
  if (!response.ok) {
    let detail = "";
    try {
      const body = await response.json();
      if (body && typeof body.detail === "string" && body.detail) {
        detail = body.detail;
      } else if (body && typeof body.error === "string" && body.error) {
        detail = body.error;
      }
    } catch {}
    throw new Error(
      detail
        ? `MISS API ${path} devolvió ${response.status}: ${detail}`
        : `MISS API ${path} devolvió ${response.status}`
    );
  }
  return response.json() as Promise<T>;
}

export async function fetchRuns(
  params: { limit?: number; offset?: number; status?: string } = {}
): Promise<RunsPage> {
  const search = new URLSearchParams();
  if (params.limit != null) search.set("limit", String(params.limit));
  if (params.offset != null) search.set("offset", String(params.offset));
  if (params.status) search.set("status", params.status);
  const query = search.toString() ? `?${search.toString()}` : "";
  return fetchJson<RunsPage>(`/api/runs${query}`);
}

export async function fetchRun(id: string): Promise<Run | null> {
  try {
    return await fetchJson<Run>(`/api/runs/${encodeURIComponent(id)}`);
  } catch (err) {
    if (err instanceof Error && err.message.includes("404")) return null;
    throw err;
  }
}

export async function fetchMetrics(): Promise<MetricsSummary> {
  return fetchJson<MetricsSummary>("/api/metrics");
}

export async function fetchTools(): Promise<ToolsList> {
  return fetchJson<ToolsList>("/api/tools");
}

export async function fetchTool(slug: string): Promise<ToolDetail | null> {
  try {
    return await fetchJson<ToolDetail>(`/api/tools/${encodeURIComponent(slug)}`);
  } catch (err) {
    if (err instanceof Error && err.message.includes("404")) return null;
    throw err;
  }
}

export async function updateTool(slug: string, patch: ToolPatch): Promise<Tool> {
  return fetchJson<Tool>(`/api/tools/${encodeURIComponent(slug)}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
}

export async function fetchModels(kind: string): Promise<LlmModelsResponse> {
  return fetchJson<LlmModelsResponse>(
    `/api/llm/models?kind=${encodeURIComponent(kind)}`
  );
}

export async function testTool(
  slug: string,
  formData: FormData
): Promise<ToolTestResult> {
  return fetchJson<ToolTestResult>(
    `/api/tools/${encodeURIComponent(slug)}/test`,
    { method: "POST", body: formData }
  );
}
