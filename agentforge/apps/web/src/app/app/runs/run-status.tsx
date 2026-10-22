import { Badge } from "@/components/ui/badge";
import { RUN_STATUS, type RunStatus } from "@/lib/runs";

export function RunStatusBadge({ status }: { status: RunStatus }) {
  const known = RUN_STATUS[status];
  return (
    <Badge tone={known.tone} data-testid="run-status" data-status={status}>
      {known.label}
    </Badge>
  );
}
