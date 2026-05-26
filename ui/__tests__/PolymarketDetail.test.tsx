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

  // The Submit Real promotion is gated behind NEXT_PUBLIC_SHOW_SUBMIT_REAL so
  // a reviewer can't accidentally trigger a live submission during a demo.
  // Set the env var around the tests that exercise the active flow and unset
  // it for the disabled-button test.
  describe("with NEXT_PUBLIC_SHOW_SUBMIT_REAL=true (operator mode)", () => {
    beforeAll(() => {
      process.env.NEXT_PUBLIC_SHOW_SUBMIT_REAL = "true";
    });
    afterAll(() => {
      delete process.env.NEXT_PUBLIC_SHOW_SUBMIT_REAL;
    });

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

  describe("default (demo mode) — Submit Real is disabled", () => {
    beforeAll(() => {
      delete process.env.NEXT_PUBLIC_SHOW_SUBMIT_REAL;
    });

    it("renders a disabled Submit Real button with an enable hint", () => {
      render(<PolymarketDetail polymarket={{ ...base, isSimulated: true }} eventId="42" />);
      const btn = screen.getByRole("button", { name: /Submit Real/i });
      expect(btn).toBeDisabled();
      expect(btn).toHaveAttribute(
        "title",
        expect.stringContaining("NEXT_PUBLIC_SHOW_SUBMIT_REAL"),
      );
      // Confirm dialog must not appear because the user can't click the
      // gated button.
      fireEvent.click(btn);
      expect(
        screen.queryByText(/This will POST to Polymarket prod review queue/i),
      ).not.toBeInTheDocument();
    });
  });
});
