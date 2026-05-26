export function SiteFooter() {
  return (
    <footer
      className="border-t border-border/60 bg-background/40"
      style={{ paddingBottom: "env(safe-area-inset-bottom)" }}
    >
      <div className="container flex flex-col items-start gap-2 py-6 text-xs text-muted-foreground md:flex-row md:items-center md:justify-between">
        <p>
          Polyglot Alpha v2 — research demo. Closed-IP evaluators are intentionally redacted.
        </p>
        <p className="font-mono break-all sm:break-normal">
          backend:{" "}
          <span className="text-foreground/80">
            {process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000"}
          </span>
        </p>
      </div>
    </footer>
  );
}
