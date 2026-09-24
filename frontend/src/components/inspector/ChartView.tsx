import { useEffect, useRef, useState } from "react";
import type { VisualizationSpec } from "vega-embed";
import { useChart } from "../../api/queries";
import { ArtifactLink } from "../ArtifactLink";

/** Vega-Lite chart (`GET /api/charts/{id}`) rendered with vega-embed (loaded lazily). */
export function ChartView({ id }: { id: string }) {
  const query = useChart(id);
  const container = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);
  const spec = query.data?.spec;

  useEffect(() => {
    const el = container.current;
    if (!el || !spec) return;
    let disposed = false;
    let finalize: (() => void) | undefined;
    setError(null);
    import("vega-embed")
      .then(({ default: embed }) =>
        embed(el, { ...spec, width: "container", autosize: { type: "fit", contains: "padding" } } as VisualizationSpec, {
          actions: false,
          renderer: "svg",
        }),
      )
      .then((result) => {
        if (disposed) result.finalize();
        else finalize = () => result.finalize();
      })
      .catch((err: unknown) => {
        if (!disposed) setError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      disposed = true;
      finalize?.();
    };
  }, [spec]);

  if (query.isPending) return <div className="muted small">Loading {id}…</div>;
  if (query.isError) return <div className="error-text">{id}: {query.error.message}</div>;
  return (
    <figure className="chart">
      <figcaption className="chart-head">
        <span className="mono artifact-id">{id}</span>
        <span className="chart-title">{query.data.title}</span>
        <span className="muted small">
          from <ArtifactLink id={query.data.dataset_id} />
        </span>
      </figcaption>
      <div className="chart-canvas" ref={container} />
      {error && <div className="error-text">Chart failed to render: {error}</div>}
    </figure>
  );
}
