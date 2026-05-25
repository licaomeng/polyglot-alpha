import { render, screen } from "@testing-library/react";
import { EmptyState } from "@/components/shared/EmptyState";

describe("EmptyState", () => {
  it("renders default title and a description when provided", () => {
    render(<EmptyState description="Trigger one to begin." />);
    expect(screen.getByText("Nothing here yet")).toBeInTheDocument();
    expect(screen.getByText("Trigger one to begin.")).toBeInTheDocument();
  });

  it("uses status role for assistive tech", () => {
    render(<EmptyState />);
    expect(screen.getByRole("status")).toBeInTheDocument();
  });
});
