/**
 * projectsClient.ts — API client for the multi-vault Project registry (v1.5 P2, ADR-0067).
 *
 *   GET  /projects                      → { projects, active_id }
 *   POST /projects/open      { path }    → Project (register existing vault)
 *   POST /projects           { name, path } → Project (create + scaffold)
 *   POST /projects/{id}/activate         → { project, active_vault_epoch }
 *
 * Errors throw ApiError (non-2xx) so the launcher can surface them. No secrets here.
 * ADR-0047 §6 Do-NOT: never cache apiBase() in a module-level const.
 */

import { apiBase } from "./base";
import { fetchWithTimeout } from "./http";
import { ApiError } from "./graphClient";

export interface Project {
  id: string;
  name: string;
  path: string;
  created_at: string;
  last_opened_at?: string | null;
}

export interface ProjectsResponse {
  projects: Project[];
  active_id: string | null;
}

export interface ActivateResponse {
  project: Project;
  active_vault_epoch: number;
}

async function _json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      // ignore parse error
    }
    throw new ApiError(res.status, `${res.status} ${detail}`);
  }
  return (await res.json()) as T;
}

/** GET /projects — the registry (projects + active_id). */
export async function fetchProjects(signal?: AbortSignal): Promise<ProjectsResponse> {
  const res = await fetchWithTimeout(
    `${apiBase()}/projects`,
    signal !== undefined ? { signal } : undefined,
  );
  return _json<ProjectsResponse>(res);
}

/** POST /projects/open — register an existing vault folder (does not switch). */
export async function openProject(path: string, signal?: AbortSignal): Promise<Project> {
  const res = await fetchWithTimeout(`${apiBase()}/projects/open`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
    ...(signal !== undefined ? { signal } : {}),
  });
  return _json<Project>(res);
}

/** POST /projects — create + scaffold a new vault (does not switch). */
export async function createProject(
  name: string,
  path: string,
  signal?: AbortSignal,
): Promise<Project> {
  const res = await fetchWithTimeout(`${apiBase()}/projects`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, path }),
    ...(signal !== undefined ? { signal } : {}),
  });
  return _json<Project>(res);
}

/** POST /projects/{id}/activate — switch the active vault at runtime. */
export async function activateProject(
  id: string,
  signal?: AbortSignal,
): Promise<ActivateResponse> {
  const res = await fetchWithTimeout(`${apiBase()}/projects/${encodeURIComponent(id)}/activate`, {
    method: "POST",
    ...(signal !== undefined ? { signal } : {}),
  });
  return _json<ActivateResponse>(res);
}
