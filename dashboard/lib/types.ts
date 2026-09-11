export type RunStatus =
  | "queued"
  | "processing"
  | "completed"
  | "failed"
  | "skipped";

export interface Run {
  id: string;
  created_at: string;
  updated_at: string;
  instance_root: string;
  sub_instance: string;
  phone: string;
  event_type: string;
  status: RunStatus;
  attachment_kind: string | null;
  attachment_url: string | null;
  transcription_text: string | null;
  transcription_cost_usd: number | null;
  transcription_duration_seconds: number | null;
  contact_name: string | null;
  mass_id_original: string | null;
  error_message: string | null;
  raw_payload: string | null;
  outbound_calls: string | null;
}

export interface OutboundCall {
  ts: string;
  method: string;
  url: string;
  status: number;
  ms: number | null;
  request_body?: string | null;
  request_headers?: Record<string, string> | null;
}

export interface RunsPage {
  items: Run[];
  limit: number;
  offset: number;
}

export interface MetricsSummary {
  total: number;
  completed: number;
  failed: number;
  queued: number;
  processing: number;
  skipped: number;
  total_cost_usd: number;
  total_duration_seconds: number;
}

export interface ToolStats {
  total: number;
  completed: number;
  failed: number;
  skipped: number;
  total_cost_usd: number;
  total_duration_seconds: number;
}

export interface Tool {
  slug: string;
  name: string;
  description: string;
  kind: string;
  enabled: boolean;
  model: string;
  prompt: string | null;
  note_prefix: string;
  updated_at: string;
}

export interface ToolWithStats extends Tool {
  stats: ToolStats;
}

export interface ToolsList {
  items: ToolWithStats[];
}

export interface ToolDetail extends ToolWithStats {
  recent_runs: Run[];
}

export interface ToolPatch {
  name?: string;
  description?: string;
  enabled?: boolean;
  model?: string;
  prompt?: string | null;
  note_prefix?: string;
}
