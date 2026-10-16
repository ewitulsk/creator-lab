import { Badge, type BadgeTone } from "@/components/ui/primitives";
import { fmtRatio } from "@/lib/format";

const FORMAT_TONE: Record<string, BadgeTone> = { long: "outline", short: "primary", live: "warning", premiere: "warning" };

export function FormatBadge({ format }: { format: string }) {
  return <Badge tone={FORMAT_TONE[format] ?? "default"}>{format}</Badge>;
}

/** Outlier score coloured by how far it is from the channel's baseline. */
export function OutlierBadge({ score }: { score: number | null | undefined }) {
  if (score === null || score === undefined) return <span className="text-muted-foreground">–</span>;
  const tone: BadgeTone = score >= 2 ? "success" : score <= 0.5 ? "destructive" : "default";
  return <Badge tone={tone}>{fmtRatio(score)}</Badge>;
}

const STATUS_TONE: Record<string, BadgeTone> = {
  done: "success",
  queued: "default",
  claimed: "primary",
  running: "primary",
  failed: "destructive",
  cancelled: "outline",
  missing: "outline",
};

export function StatusBadge({ status }: { status: string }) {
  return <Badge tone={STATUS_TONE[status] ?? "default"}>{status}</Badge>;
}
