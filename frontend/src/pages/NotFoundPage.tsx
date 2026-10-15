import { Link } from "@tanstack/react-router";
import { EmptyState } from "@/components/ui/primitives";

export function NotFoundPage() {
  return (
    <EmptyState title="Page not found">
      <Link to="/" className="text-primary underline">
        Back to the dashboard
      </Link>
    </EmptyState>
  );
}
