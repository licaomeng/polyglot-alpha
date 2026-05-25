import { render, screen } from "@testing-library/react";
import { Badge } from "@/components/ui/badge";

describe("Badge", () => {
  it("renders children text", () => {
    render(<Badge>LIVE</Badge>);
    expect(screen.getByText("LIVE")).toBeInTheDocument();
  });

  it("applies the success variant styles", () => {
    render(<Badge variant="success">ok</Badge>);
    const el = screen.getByText("ok");
    expect(el.className).toMatch(/emerald/);
  });
});
