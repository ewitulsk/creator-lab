import * as Plot from "@observablehq/plot";
import { useEffect, useLayoutEffect, useRef, useState } from "react";

type PlotOptions = Plot.PlotOptions;

/**
 * Renders an Observable Plot chart at the container's width (re-rendered on resize).
 * `onPick` fires with the datum under the pointer when the chart is clicked (needs a
 * mark with a pointer transform, e.g. `tip: true`).
 */
export function PlotFigure<T = unknown>({
  options,
  onPick,
  className,
}: {
  options: PlotOptions;
  onPick?: (datum: T) => void;
  className?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const pickRef = useRef(onPick);
  const [width, setWidth] = useState(0);

  useEffect(() => {
    pickRef.current = onPick;
  }, [onPick]);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    // Measure now (ResizeObserver may not fire until the page paints), then track resizes.
    setWidth(Math.round(el.clientWidth) || 640);
    const ro = new ResizeObserver(([entry]) => {
      const w = Math.round(entry.contentRect.width);
      if (w > 0) setWidth((prev) => (Math.abs(prev - w) > 8 ? w : prev));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    const el = ref.current;
    if (!el || width === 0) return;
    const fig = Plot.plot({ width, style: { background: "transparent", fontSize: "11px" }, ...options });
    el.replaceChildren(fig);
    const click = () => {
      const value = (fig as unknown as { value?: T }).value;
      if (value !== undefined && value !== null && pickRef.current) pickRef.current(value);
    };
    fig.addEventListener("click", click);
    if (pickRef.current) fig.style.cursor = "pointer";
    return () => {
      fig.removeEventListener("click", click);
      fig.remove();
    };
  }, [options, width]);

  return <div ref={ref} className={className ?? "w-full min-w-0 overflow-hidden text-foreground"} />;
}
