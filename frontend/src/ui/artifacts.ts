/** Artifact ids in message text (spec §3.2 ids): `ds_…` dataset, `ch_…` chart, `rp_…` report.
 * Has a capture group so `String.split` yields tokens at odd indices. */
export const ARTIFACT_SPLIT = /(\b(?:ds|ch|rp)_[0-9a-f]{12}\b)/;

export const ARTIFACT_EXACT = /^(?:ds|ch|rp)_[0-9a-f]{12}$/;

export type ArtifactKind = "dataset" | "chart" | "report";

export function artifactKind(id: string): ArtifactKind {
  if (id.startsWith("ds_")) return "dataset";
  if (id.startsWith("ch_")) return "chart";
  return "report";
}

/** Fragment used for artifact links inside rendered markdown (survives react-markdown's URL filter). */
export const ARTIFACT_HREF_PREFIX = "#artifact-";

const AGENT_COLORS: Record<string, string> = {
  user: "#0f766e",
  orchestrator: "#6d28d9",
  data: "#1d4ed8",
  compare: "#0e7490",
  insight: "#b45309",
  report: "#be185d",
};

export function agentColor(name: string): string {
  const known = AGENT_COLORS[name];
  if (known) return known;
  let h = 0;
  for (const ch of name) h = (h * 31 + ch.charCodeAt(0)) % 360;
  return `hsl(${h} 55% 38%)`;
}
