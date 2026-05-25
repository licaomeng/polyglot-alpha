import { render, screen, fireEvent } from "@testing-library/react";
import { PolymarketDetail } from "@/components/polymarket/PolymarketDetail";

describe("PolymarketDetail", () => {
  const base = {
    builderCode: "polyglot_alpha",
    marketUrl: "https://polymarket.com/event/demo",
    marketId: "market_xyz",
    status: "pending_review",
    revenueStream: [],
  };

  it("renders DRY_RUN mode badge when isSimulated=true", () => {
    render(<PolymarketDetail polymarket={{ ...base, isSimulated: true }} eventId="42" />);
    expect(screen.getByText("DRY_RUN")).toBeInTheDocument();
    expect(screen.getByText("polyglot_alpha")).toBeInTheDocument();
  });

  it("renders LIVE mode badge when isSimulated=false", () => {
    render(<PolymarketDetail polymarket={{ ...base, isSimulated: false }} eventId="42" />);
    expect(screen.getByText("LIVE")).toBeInTheDocument();
  });

  it("opens a confirm dialog before submitting real", () => {
    render(<PolymarketDetail polymarket={{ ...base, isSimulated: true }} eventId="42" />);
    fireEvent.click(screen.getByRole("button", { name: /Submit Real/i }));
    expect(
      screen.getByText(/This will POST to Polymarket prod review queue/i),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Cancel/i })).toBeInTheDocument();
  });

  it("shows the API payload when toggled open", () => {
    render(
      <PolymarketDetail
        polymarket={{ ...base, isSimulated: true, payload: { foo: "bar" } }}
        eventId="42"
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /View API Payload/i }));
    expect(screen.getByText(/"foo"/)).toBeInTheDocument();
  });
});
