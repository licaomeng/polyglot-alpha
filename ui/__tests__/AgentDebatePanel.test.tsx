import { render, screen, fireEvent } from "@testing-library/react";
import { AgentDebatePanel } from "@/components/event/AgentDebatePanel";
import type { EventDetail, PhaseState } from "@/lib/api";

function makeEvent(overrides: Partial<EventDetail> = {}): EventDetail {
  const phases: PhaseState[] = [
    { name: "Event Ingestion", status: "completed" },
    { name: "USDC Auction", status: "completed" },
    { name: "Translation Pipeline", status: "pending" },
    { name: "11-Judge Panel", status: "pending" },
    { name: "On-chain Anchor", status: "pending" },
    { name: "Polymarket V2 Submission", status: "pending" },
    { name: "Streaming Revenue", status: "pending" },
  ];
  return {
    id: "test-1",
    headline: "test headline",
    source: "test",
    status: "pending",
    ingestedAt: new Date().toISOString(),
    mode: "mock",
    phases,
    ...overrides,
  };
}

describe("AgentDebatePanel", () => {
  it("renders the empty placeholder when the lifecycle hasn't reached L3", () => {
    render(<AgentDebatePanel event={makeEvent()} />);
    expect(screen.getByTestId("agent-debate-panel-empty")).toBeInTheDocument();
    expect(screen.getByText(/Agent debate/i)).toBeInTheDocument();
    // Demo trigger is available from the empty state.
    expect(screen.getByRole("button", { name: /Show demo data/i })).toBeInTheDocument();
  });

  it("renders the full debate panel when phase 2 has reached L3", () => {
    const event = makeEvent();
    event.phases[2] = {
      ...event.phases[2],
      status: "running",
      details: {
        subPhases: {
          "L1 Analysts": "completed",
          "L2 Translators": "completed",
          "L3 Critics": "running",
        },
      },
    };
    render(<AgentDebatePanel event={event} />);
    expect(screen.getByTestId("agent-debate-panel")).toBeInTheDocument();
    // Awaiting placeholders show when no candidate data is attached yet.
    expect(screen.getByText(/awaiting L2 translator candidate A/)).toBeInTheDocument();
    expect(screen.getByText(/awaiting L2 translator candidate B/)).toBeInTheDocument();
    expect(screen.getByText(/awaiting moderator verdict/)).toBeInTheDocument();
    expect(screen.getByText(/awaiting refine pass/)).toBeInTheDocument();
  });

  it("injects realistic mock data when demo mode is toggled on", () => {
    render(<AgentDebatePanel event={makeEvent()} />);
    const showButton = screen.getByRole("button", { name: /Show demo data/i });
    fireEvent.click(showButton);
    // Full panel renders, with the moderator pick and refine after copy.
    expect(screen.getByTestId("agent-debate-panel")).toBeInTheDocument();
    expect(screen.getByText(/moderator pick/i)).toBeInTheDocument();
    expect(screen.getAllByText(/Candidate A/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Candidate B/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/monthly fixing release/i).length).toBeGreaterThan(0);
  });

  it("reads candidates / moderator / refine attached to phase 2 details", () => {
    const event = makeEvent();
    event.phases[2] = {
      ...event.phases[2],
      status: "completed",
      details: {
        subPhases: {
          "L1 Analysts": "completed",
          "L2 Translators": "completed",
          "L3 Critics": "completed",
          "L4 Moderator": "completed",
          "L5 Refine": "completed",
        },
        candidates: [
          {
            id: "X",
            question: "Will X happen by 2026?",
            critic: { issues: ["too vague"], strengths: ["clear deadline"] },
          },
          {
            id: "Y",
            question: "Will Y happen by 2026?",
            critic: { issues: [], strengths: ["specific"] },
          },
        ],
        moderator: {
          pickedId: "Y",
          reasoning: "Y is more tradeable.",
        },
        refine: {
          finalQuestion: "Will Y happen with ≥5% margin by 2026?",
          diffSummary: "added margin clause",
        },
      },
    };
    render(<AgentDebatePanel event={event} />);
    expect(screen.getByText(/Will X happen by 2026/)).toBeInTheDocument();
    expect(screen.getByText(/Will Y happen by 2026/)).toBeInTheDocument();
    expect(screen.getByText(/Y is more tradeable/)).toBeInTheDocument();
    expect(screen.getByText(/margin clause/)).toBeInTheDocument();
  });
});
