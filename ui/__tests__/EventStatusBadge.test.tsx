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
});
