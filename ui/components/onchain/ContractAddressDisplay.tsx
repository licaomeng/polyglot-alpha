"use client";

import { Copy, Check } from "lucide-react";
import { useState } from "react";
import { shortAddr } from "@/lib/utils";

export function ContractAddressDisplay({
  address,
  label,
}: {
  address: string;
  label?: string;
}) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="flex items-center gap-2 rounded-md border border-border/60 bg-card/40 px-3 py-2">
      {label && (
        <span className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">
          {label}
        </span>
      )}
      <span className="font-mono text-xs">{shortAddr(address, 8, 6)}</span>
      <button
        type="button"
        onClick={() => {
          navigator.clipboard?.writeText(address).catch(() => {});
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        }}
        aria-label={`Copy address ${address}`}
        className="ml-auto rounded p-1 text-muted-foreground hover:bg-accent/10 hover:text-foreground"
      >
        {copied ? <Check className="h-3.5 w-3.5" aria-hidden /> : <Copy className="h-3.5 w-3.5" aria-hidden />}
      </button>
    </div>
  );
}
