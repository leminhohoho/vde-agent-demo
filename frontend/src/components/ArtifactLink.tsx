import type { ReactNode } from "react";
import { useUi } from "../ui/UiContext";
import { ARTIFACT_SPLIT, artifactKind } from "../ui/artifacts";

const KIND_LABEL = { dataset: "Dataset", chart: "Chart", report: "Report" } as const;

/** Clickable artifact id; opens the Inspector's Artifact tab. */
export function ArtifactLink({ id, children }: { id: string; children?: ReactNode }) {
  const { openArtifact } = useUi();
  const kind = artifactKind(id);
  return (
    <button
      type="button"
      className={`artifact-link artifact-${kind}`}
      title={`Open ${KIND_LABEL[kind].toLowerCase()} ${id}`}
      onClick={(e) => {
        e.stopPropagation();
        openArtifact(id);
      }}
    >
      {children ?? id}
    </button>
  );
}

/** Plain text with every `ds_/ch_/rp_` id rendered as an ArtifactLink. */
export function ArtifactText({ text }: { text: string }) {
  const parts = text.split(ARTIFACT_SPLIT);
  return (
    <>
      {parts.map((part, i) => (i % 2 === 1 ? <ArtifactLink key={i} id={part} /> : part))}
    </>
  );
}
