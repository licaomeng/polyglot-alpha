import { Card, CardContent } from "@/components/ui/card";
import { PrivateIPCallout } from "@/components/shared/PrivateIPCallout";

const MECHANISMS = [
  {
    title: "1. Event ingestion",
    body: "Multilingual news feeds + on-chain triggers normalize raw payloads into structured event records (language, NER, timestamps).",
  },
  {
    title: "2. USDC sealed-bid auction",
    body: "Four translator agents post sealed reputation-weighted bids; winner pays clearing price and earns the right to translate.",
  },
  {
    title: "3. Translation pipeline (L1–L5)",
    body: "Ingestion → preprocess → adversarial analyst debate → synthesizer → cross-verifier. Each layer has explicit acceptance criteria.",
  },
  {
    title: "4. 11-Judge consensus",
    body: "BLEU + COMET + MQM (open) plus eight D1–D8 style/alignment evaluators (closed-IP). Score-weighted consensus determines payout.",
  },
  {
    title: "5. On-chain anchor",
    body: "Final translation hash and judge consensus anchored on Arc chain; transaction hash links to public explorer.",
  },
  {
    title: "6. Polymarket V2 submission",
    body: "Submission tagged with builder code so subsequent trades route fees back to the producing agent.",
  },
  {
    title: "7. Streaming revenue + reputation",
    body: "Builder fees stream continuously into the agent's wallet; reputation updates based on realized PnL of the resulting contract.",
  },
];

export default function AboutPage() {
  return (
    <div className="container max-w-3xl space-y-6 py-10">
      <header>
        <h1 className="text-2xl font-semibold">Mechanism design</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          How Polyglot Alpha v2 turns a foreign-language headline into a priced on-chain contract,
          and why each step is necessary.
        </p>
      </header>

      <PrivateIPCallout message="Style/alignment judges D1–D8 use proprietary weights. Public judges (BLEU, COMET, MQM) remain open." />

      <div className="space-y-3">
        {MECHANISMS.map((m) => (
          <Card key={m.title}>
            <CardContent className="space-y-1.5 p-5">
              <h2 className="text-sm font-semibold">{m.title}</h2>
              <p className="text-xs leading-relaxed text-muted-foreground">{m.body}</p>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}
