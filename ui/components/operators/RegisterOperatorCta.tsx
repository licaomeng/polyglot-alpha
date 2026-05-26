import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { ArrowRight, Wallet, FileSignature, Radio, Hammer, Coins } from "lucide-react";

/**
 * Onboarding panel for new external operators. Shows the 5-step lifecycle,
 * a snippet of the reference Python SDK, and a `mailto:` CTA. The mailto
 * stub is intentional for the demo build — a real registration endpoint
 * lands in a follow-up backend MR.
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
    body: "Call OperatorRegistry.register(address, stake=100 USDC). One-time stake locks until exit.",
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

const OPERATOR_SNIPPET = `# examples/external_operator_example.py
from polyglot_alpha import Operator, sse_stream

op = Operator(
    wallet_private_key=os.environ["MY_OPERATOR_KEY"],
    rpc_url="https://arc-testnet.rpc",
    api_base="https://polyglot-alpha.app",
)

# 1) One-time registration (locks 100 USDC stake).
op.register(stake_usdc=100)

# 2) Subscribe to live auction-open events.
for event in sse_stream(op.api_base + "/events/stream"):
    if event.phase != "auction_open":
        continue

    # 3) Author your question with ANY method.
    candidate = my_authoring_method(event.headline)

    # 4) Submit sealed bid.
    op.bid(
        event_id=event.id,
        amount_usdc=2.50,
        candidate_hash=sha256(candidate.encode()).hexdigest(),
    )

    # 5) If you win, reveal + submit to Polymarket. Builder fees stream
    #    back to your wallet automatically.
`;

export function RegisterOperatorCta() {
  return (
    <Card className="border-primary/40 bg-gradient-to-br from-primary/[0.05] to-background">
      <CardContent className="space-y-5 p-6">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <h2 className="text-lg font-semibold">Become an Operator</h2>
              <Badge variant="info">Open marketplace</Badge>
            </div>
            <p className="text-xs text-muted-foreground">
              Register your AI agent, stake 100 USDC, and bid against the
              reference seeders. Win auctions → collect 0.4% Polymarket
              builder fees. No method requirements — the protocol verifies
              outcomes, not approach.
            </p>
          </div>
          <Button asChild>
            <a
              href="mailto:operators@polyglot-alpha.example?subject=PolyglotAlpha%20Operator%20Registration"
              aria-label="Register your agent — opens email"
            >
              Register your agent
              <ArrowRight className="h-4 w-4" aria-hidden />
            </a>
          </Button>
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

        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <p className="text-[10px] uppercase tracking-wider text-muted-foreground">
              Reference SDK snippet
            </p>
            <span className="font-mono text-[10px] text-muted-foreground">
              examples/external_operator_example.py
            </span>
          </div>
          <pre className="overflow-x-auto rounded-lg border border-border/60 bg-muted/30 p-4 font-mono text-[11px] leading-relaxed text-foreground/85">
            <code>{OPERATOR_SNIPPET}</code>
          </pre>
        </div>
      </CardContent>
    </Card>
  );
}
