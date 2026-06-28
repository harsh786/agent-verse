import type { JSX } from "react";
import { Loader2 } from "lucide-react";

interface LoadingSpinnerProps {
  size?: "sm" | "md" | "lg";
  className?: string;
}

export function LoadingSpinner({ size = "md", className = "" }: LoadingSpinnerProps): JSX.Element {
  const sizes = { sm: "h-4 w-4", md: "h-5 w-5", lg: "h-8 w-8" };
  return (
    <div
      className={`flex items-center justify-center h-64 ${className}`}
      role="status"
      aria-label="Loading"
    >
      <Loader2 className={`animate-spin text-muted-foreground ${sizes[size]}`} />
    </div>
  );
}
