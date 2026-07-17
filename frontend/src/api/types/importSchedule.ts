/**
 * Scheduled folder import API contract types (FE-QUAL-11 split of api/types.ts).
 * GET/PUT /import-schedule (ADR-0020 §4.6)
 */

export type ImportFrequency = "15m" | "1h" | "6h" | "daily";

export type ImportLastStatus =
  "ok" | "error" | "running" | "skipped_disabled" | "dir_missing" | null;

export interface ImportSchedule {
  enabled: boolean;
  source_dir: string | null;
  frequency: ImportFrequency;
  // P3-c: wider Source-Watch types (null → default wider set / none / no cap)
  allowed_extensions: string | null; // comma-separated, e.g. ".pdf,.csv"
  excluded_folders: string | null; // comma-separated folder names
  max_size_mb: number | null; // null → no cap
  last_run_at: string | null; // ISO-8601
  last_status: ImportLastStatus;
  last_imported_count: number;
  last_error: string | null;
}

export interface ImportSchedulePutBody {
  enabled?: boolean;
  source_dir?: string | null;
  frequency?: ImportFrequency;
  // P3-c: "" clears allowed/excluded to default/none; 0 clears the size cap
  allowed_extensions?: string;
  excluded_folders?: string;
  max_size_mb?: number;
}

export interface ImportSchedulePutResponse extends ImportSchedule {
  dir_ok: boolean;
  dir_message: string | null;
}
