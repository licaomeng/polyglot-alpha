"use client";

import { useEffect, useState } from "react";

// Read-only Arc-chain client. Falls back gracefully when RPC is unreachable.
// Uses a raw JSON-RPC fetch to avoid pulling in the full viem bundle (~50 MB)
// for a single eth_blockNumber call.
async function getBlockNumber(rpcUrl: string): Promise<bigint> {
  const r = await fetch(rpcUrl, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      jsonrpc: "2.0",
      method: "eth_blockNumber",
      params: [],
      id: 1,
    }),
  });
  const j = (await r.json()) as { result?: string; error?: { message?: string } };
  if (j.error) {
    throw new Error(j.error.message ?? "JSON-RPC error");
  }
  if (typeof j.result !== "string") {
    throw new Error("Malformed JSON-RPC response");
  }
  return BigInt(j.result);
}

export function useArcExplorer(rpcUrl?: string) {
  const [blockNumber, setBlockNumber] = useState<bigint | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!rpcUrl) return;
    let mounted = true;
    getBlockNumber(rpcUrl)
      .then((bn) => {
        if (mounted) setBlockNumber(bn);
      })
      .catch((err: unknown) => {
        if (mounted) setError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      mounted = false;
    };
  }, [rpcUrl]);

  return { blockNumber, error };
}
