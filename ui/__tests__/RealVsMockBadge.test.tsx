import { render, screen } from "@testing-library/react";
import { RealVsMockBadge } from "@/components/shared/RealVsMockBadge";

describe("RealVsMockBadge", () => {
  it("renders the Live label when mode is live", () => {
    render(<RealVsMockBadge mode="live" />);
    expect(screen.getByLabelText("Live data")).toBeInTheDocument();
    expect(screen.getByText("Live")).toBeInTheDocument();
  });

  it("renders the Mock label when mode is mock", () => {
    render(<RealVsMockBadge mode="mock" />);
    expect(screen.getByText("Mock")).toBeInTheDocument();
  });

  it("renders the Historical label when mode is historical", () => {
    render(<RealVsMockBadge mode="historical" />);
    expect(screen.getByText("Historical")).toBeInTheDocument();
  });
});
