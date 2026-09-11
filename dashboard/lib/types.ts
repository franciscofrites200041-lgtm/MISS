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
