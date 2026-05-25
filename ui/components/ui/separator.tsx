import { cn } from "@/lib/utils";

export function Separator({ className }: { className?: string }) {
  return <hr className={cn("border-t border-border/70 my-4", className)} aria-hidden />;
}
