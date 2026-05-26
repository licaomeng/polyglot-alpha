import { render, screen } from "@testing-library/react";
import { EventStatusBadge } from "@/components/event/EventStatusBadge";

describe("EventStatusBadge", () => {
  it("renders Settled label for completed status", () => {
    render(<EventStatusBadge status="completed" />);
    expect(screen.getByText("Settled")).toBeInTheDocument();
  });

  it("renders Running for running status", () => {
    render(<EventStatusBadge status="running" />);
    expect(screen.getByText("Running")).toBeInTheDocument();
  });

  it("falls back to raw status if unknown", () => {
    render(<EventStatusBadge status={"weird" as any} />);
    expect(screen.getByText("weird")).toBeInTheDocument();
  });

  // Backend serializes SQLAlchemy enum values as uppercase strings. UI must
  // map them to the same user-facing labels as the lowercase synthetic ones,
  // otherwise events appear with raw `EVALUATING` / `SUBMITTED` placeholders.
  it.each([
    ["PENDING", "Queued"],
    ["AUCTION_OPEN", "Auctioning"],
    ["AUCTION_SETTLED", "Settled bid"],
    ["TRANSLATING", "Translating"],
    ["EVALUATING", "Judging"],
    ["REJECTED", "Rejected"],
    ["COMMITTED", "Anchored"],
    ["SUBMITTED", "Settled"],
    ["FAILED", "Failed"],
  ])("maps backend enum %s -> %s", (raw, label) => {
    render(<EventStatusBadge status={raw as any} />);
    expect(screen.getByText(label)).toBeInTheDocument();
  });

  it("renders 'Unknown' for null/empty status instead of breaking layout", () => {
    render(<EventStatusBadge status={"" as any} />);
    expect(screen.getByText("Unknown")).toBeInTheDocument();
  });
});
