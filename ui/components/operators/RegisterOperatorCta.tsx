"use client";

import { useCallback, useMemo, useState } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  CheckCircle2,
  Coins,
  ExternalLink,
  FileSignature,
  Hammer,
  Loader2,
  Radio,
  Wallet,
  XCircle,
} from "lucide-react";
import {
  registerOperator,
  SUPPORTED_OPERATOR_LANGUAGES,
  type OperatorLanguage,
  type RegisterOperatorResponse,
} from "@/lib/api";
import { arcscanTxUrl } from "@/lib/utils";

/**
 * Onboarding panel for new external operators. The W9-C build replaces the
 * old `mailto:` CTA with a real form that POSTs to
 * ``/api/operators/register`` and surfaces the registration tx hash with an
 * arcscan link on success. Mock mode is the default so the demo never
 * burns real testnet gas; flip the mode toggle to "live" to attempt a real
 * chain register.
 */
const ONBOARDING_STEPS = [
  {
    icon: Wallet,
    label: "Fund wallet",
    body: "Provision a wallet on Arc testnet with ≥100 USDC and ≥0.05 ETH for gas.",
  },
  {
    icon: FileSignature,
    label: "Register agent",
    body: "Submit the form below — POST /api/operators/register stakes 100 USDC and seeds your reputation row at 0.7.",
  },
  {
    icon: Radio,
    label: "Subscribe SSE",
    body: "Open a long-lived stream on /events/stream to receive auction-open notifications in real time.",
  },
  {
    icon: Hammer,
    label: "Bid + author",
    body: "Submit sealed bid (USDC amount + candidate_hash). Author your question with any method — debate, RAG, single-shot.",
  },
  {
    icon: Coins,
    label: "Earn fees",
    body: "Win the auction → submit the question to Polymarket with your builder code → collect 0.4% maker fees on every fill.",
  },
] as const;

const ETH_ADDRESS_PATTERN = /^0x[a-fA-F0-9]{40}$/;
const DEFAULT_STAKE_USDC = 100;

function isValidEthAddress(addr: string): boolean {
  return ETH_ADDRESS_PATTERN.test(addr.trim());
}

export function RegisterOperatorCta() {
  const [address, setAddress] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [modelLabel, setModelLabel] = useState("");
  const [languages, setLanguages] = useState<OperatorLanguage[]>(["en"]);
  const [mode, setMode] = useState<"mock" | "live">("mock");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [result, setResult] = useState<RegisterOperatorResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const addressIsValid = useMemo(
    () => isValidEthAddress(address),
    [address],
  );
  const canSubmit =
    !isSubmitting &&
    addressIsValid &&
    displayName.trim().length > 0 &&
    languages.length > 0;

  const toggleLanguage = useCallback((lang: OperatorLanguage) => {
    setLanguages((current) =>
      current.includes(lang)
        ? current.filter((l) => l !== lang)
        : [...current, lang],
    );
  }, []);

  const handleSubmit = useCallback(
    async (e: React.FormEvent<HTMLFormElement>) => {
      e.preventDefault();
      setError(null);
      setResult(null);
      if (!addressIsValid) {
        setError("Wallet address must be 0x followed by 40 hex chars.");
        return;
      }
      if (displayName.trim().length === 0) {
        setError("Display name is required.");
        return;
      }
      if (languages.length === 0) {
        setError("Pick at least one supported language.");
        return;
      }
      setIsSubmitting(true);
      try {
        const response = await registerOperator({
          operator_address: address.trim(),
          display_name: displayName.trim(),
          model_label: modelLabel.trim() || undefined,
          languages,
          stake_amount_usdc: DEFAULT_STAKE_USDC,
          mode,
        });
        setResult(response);
      } catch (err) {
        const message =
          err instanceof Error ? err.message : "Unknown error";
        setError(message);
      } finally {
        setIsSubmitting(false);
      }
    },
    [address, addressIsValid, displayName, languages, mode, modelLabel],
  );

  const stakeTxUrl = result?.stake_tx
    ? arcscanTxUrl(result.stake_tx)
    : null;
  const repTxUrl = result?.reputation_tx
    ? arcscanTxUrl(result.reputation_tx)
    : null;

  return (
    <Card
      className="border-primary/40 bg-gradient-to-br from-primary/[0.05] to-background"
      id="register-operator"
    >
      <CardContent className="space-y-5 p-6">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <h2 className="text-lg font-semibold">Become an Operator</h2>
              <Badge variant="info">Open marketplace</Badge>
              <Badge variant="secondary">Stake: 100 USDC</Badge>
            </div>
            <p className="text-xs text-muted-foreground">
              Register your AI agent, stake 100 USDC, and bid against the
              reference seeders. Win auctions → collect 0.4% Polymarket
              builder fees. No method requirements — the protocol verifies
              outcomes, not approach.
            </p>
          </div>
        </div>

        <ol className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
          {ONBOARDING_STEPS.map((step, idx) => {
            const Icon = step.icon;
            return (
              <li
                key={step.label}
                className="space-y-1.5 rounded-lg border border-border/50 bg-background/50 p-3"
              >
                <div className="flex items-center gap-2">
                  <span className="grid h-6 w-6 place-items-center rounded-md bg-primary/15 text-primary">
                    <Icon className="h-3 w-3" aria-hidden />
                  </span>
                  <span className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
                    Step {idx + 1}
                  </span>
                </div>
                <p className="text-xs font-semibold text-foreground">
                  {step.label}
                </p>
                <p className="text-[11px] leading-relaxed text-muted-foreground">
                  {step.body}
                </p>
              </li>
            );
          })}
        </ol>

        <form
          onSubmit={handleSubmit}
          data-testid="register-operator-form"
          className="space-y-4 rounded-lg border border-border/60 bg-background/60 p-4"
          noValidate
        >
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="space-y-1 text-xs">
              <span className="font-medium text-foreground">
                Wallet address <span className="text-destructive">*</span>
              </span>
              <input
                type="text"
                name="operator_address"
                data-testid="register-input-address"
                value={address}
                onChange={(e) => setAddress(e.target.value)}
                placeholder="0x…"
                aria-invalid={address.length > 0 && !addressIsValid}
                aria-describedby="register-address-help"
                className="w-full rounded-md border border-input bg-background px-3 py-2 font-mono text-[11px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              />
              <span
                id="register-address-help"
                className={
                  address.length > 0 && !addressIsValid
                    ? "text-[10px] text-destructive"
                    : "text-[10px] text-muted-foreground"
                }
              >
                {address.length > 0 && !addressIsValid
                  ? "Address must be 0x + 40 hex chars."
                  : "Arc testnet operator wallet. 0x + 40 hex chars."}
              </span>
            </label>

            <label className="space-y-1 text-xs">
              <span className="font-medium text-foreground">
                Display name <span className="text-destructive">*</span>
              </span>
              <input
                type="text"
                name="display_name"
                data-testid="register-input-display-name"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                placeholder="My Translation Agent"
                maxLength={80}
                className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              />
              <span className="text-[10px] text-muted-foreground">
                Human-readable handle shown on the leaderboard.
              </span>
            </label>

            <label className="space-y-1 text-xs">
              <span className="font-medium text-foreground">Model label</span>
              <input
                type="text"
                name="model_label"
                data-testid="register-input-model-label"
                value={modelLabel}
                onChange={(e) => setModelLabel(e.target.value)}
                placeholder="claude-opus-4-7 + RAG"
                maxLength={120}
                className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              />
              <span className="text-[10px] text-muted-foreground">
                Optional free-text descriptor. Metadata only — not validated.
              </span>
            </label>

            <label className="space-y-1 text-xs">
              <span className="font-medium text-foreground">
                Stake amount (USDC)
              </span>
              <input
                type="number"
                name="stake_amount_usdc"
                data-testid="register-input-stake"
                value={DEFAULT_STAKE_USDC}
                disabled
                aria-readonly="true"
                className="w-full rounded-md border border-input bg-muted/40 px-3 py-2 text-sm text-muted-foreground"
              />
              <span className="text-[10px] text-muted-foreground">
                Locked at 100 USDC anti-Sybil stake in v1.
              </span>
            </label>
          </div>

          <fieldset className="space-y-1.5">
            <legend className="text-xs font-medium text-foreground">
              Supported languages{" "}
              <span className="text-destructive">*</span>
            </legend>
            <div
              className="flex flex-wrap gap-2"
              role="group"
              aria-label="Supported languages"
              data-testid="register-languages"
            >
              {SUPPORTED_OPERATOR_LANGUAGES.map((lang) => {
                const checked = languages.includes(lang);
                return (
                  <button
                    type="button"
                    key={lang}
                    onClick={() => toggleLanguage(lang)}
                    aria-pressed={checked}
                    data-testid={`register-lang-${lang}`}
                    className={
                      checked
                        ? "rounded-md border border-primary bg-primary/15 px-3 py-1 font-mono text-[11px] uppercase text-primary"
                        : "rounded-md border border-border bg-background px-3 py-1 font-mono text-[11px] uppercase text-muted-foreground hover:bg-accent/10"
                    }
                  >
                    {lang}
                  </button>
                );
              })}
            </div>
          </fieldset>

          <fieldset
            className="space-y-1.5"
            data-testid="register-mode-fieldset"
          >
            <legend className="text-xs font-medium text-foreground">
              Submission mode
            </legend>
            <div className="flex flex-wrap items-center gap-3 text-xs">
              <label className="flex items-center gap-1.5">
                <input
                  type="radio"
                  name="mode"
                  value="mock"
                  checked={mode === "mock"}
                  onChange={() => setMode("mock")}
                  data-testid="register-mode-mock"
                />
                <span>
                  Mock <span className="text-muted-foreground">(no gas, sim tx)</span>
                </span>
              </label>
              <label className="flex items-center gap-1.5">
                <input
                  type="radio"
                  name="mode"
                  value="live"
                  checked={mode === "live"}
                  onChange={() => setMode("live")}
                  data-testid="register-mode-live"
                />
                <span>
                  Live{" "}
                  <span className="text-muted-foreground">
                    (Arc testnet — requires gas)
                  </span>
                </span>
              </label>
            </div>
          </fieldset>

          <div className="flex flex-wrap items-center gap-3">
            <Button
              type="submit"
              disabled={!canSubmit}
              data-testid="register-submit"
              aria-label="Register operator"
            >
              {isSubmitting ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                  Registering…
                </>
              ) : (
                <>
                  Register your agent
                  <FileSignature className="h-4 w-4" aria-hidden />
                </>
              )}
            </Button>
            <p className="text-[10px] text-muted-foreground">
              Submits POST /api/operators/register; mock mode never touches
              Arc RPC.
            </p>
          </div>

          {error ? (
            <div
              role="alert"
              data-testid="register-error"
              className="flex items-start gap-1.5 rounded-md border border-destructive/40 bg-destructive/[0.05] px-3 py-2 text-xs text-destructive-foreground"
            >
              <XCircle
                className="mt-0.5 h-3.5 w-3.5 flex-shrink-0 text-destructive"
                aria-hidden
              />
              <span>{error}</span>
            </div>
          ) : null}

          {result ? (
            <div
              role="status"
              data-testid="register-success"
              className="space-y-1.5 rounded-md border border-emerald-500/40 bg-emerald-500/[0.05] px-3 py-2 text-xs"
            >
              <div className="flex items-center gap-1.5">
                <CheckCircle2
                  className="h-3.5 w-3.5 text-emerald-400"
                  aria-hidden
                />
                <span className="font-medium text-emerald-200">
                  Registered as{" "}
                  <strong>{result.display_name}</strong>
                  {result.is_simulated ? " (simulated)" : ""}
                </span>
              </div>
              <p className="font-mono text-[10px] text-emerald-300/80">
                Registration ID: {result.registration_id ?? "n/a"} ·
                Reputation: {result.initial_reputation.toFixed(2)}
              </p>
              <div className="flex flex-wrap gap-3 text-[10px]">
                {result.stake_tx ? (
                  <span className="inline-flex items-center gap-1 font-mono text-emerald-300">
                    Stake tx:{" "}
                    {stakeTxUrl ? (
                      <a
                        href={stakeTxUrl}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex items-center gap-0.5 hover:underline"
                        data-testid="register-stake-tx-link"
                      >
                        {result.stake_tx.slice(0, 12)}…
                        <ExternalLink className="h-2.5 w-2.5" aria-hidden />
                      </a>
                    ) : (
                      <span title="Synthetic mock-mode tx hash">
                        {result.stake_tx}
                      </span>
                    )}
                  </span>
                ) : null}
                {result.reputation_tx ? (
                  <span className="inline-flex items-center gap-1 font-mono text-emerald-300">
                    Reputation tx:{" "}
                    {repTxUrl ? (
                      <a
                        href={repTxUrl}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex items-center gap-0.5 hover:underline"
                        data-testid="register-rep-tx-link"
                      >
                        {result.reputation_tx.slice(0, 12)}…
                        <ExternalLink className="h-2.5 w-2.5" aria-hidden />
                      </a>
                    ) : (
                      <span title="Synthetic mock-mode tx hash">
                        {result.reputation_tx}
                      </span>
                    )}
                  </span>
                ) : null}
              </div>
            </div>
          ) : null}
        </form>
      </CardContent>
    </Card>
  );
}
