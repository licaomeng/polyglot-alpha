import { render, screen, fireEvent } from "@testing-library/react";
import { JudgePanel } from "@/components/judge/JudgePanel";
import type { JudgeScore } from "@/lib/api";

const JUDGES: JudgeScore[] = [
  { judge: "BLEU", score: 0.62, category: "translation" },
  { judge: "COMET", score: 0.88, category: "translation" },
  { judge: "MQM", score: 0.75, category: "translation" },
  { judge: "D1", score: 0.95, category: "style", passed: true, notes: "Facts verified." },
  { judge: "D5", score: 0.6, category: "alignment", passed: false, notes: "Ambiguous resolver." },
  { judge: "D8", score: 0.92, category: "style", passed: true, notes: "Canonical." },
];

describe("JudgePanel", () => {
  it("renders translation judges with threshold pass/fail", () => {
    render(<JudgePanel judges={JUDGES} />);
    expect(screen.getByText("BLEU")).toBeInTheDocument();
    expect(screen.getByText("COMET")).toBeInTheDocument();
    expect(screen.getByText("MQM")).toBeInTheDocument();
  });

  it("shows a UMA dispute prevention badge on D5", () => {
    render(<JudgePanel judges={JUDGES} />);
    expect(screen.getByText(/UMA dispute prevention/i)).toBeInTheDocument();
  });

  it("expands judge reasoning on click", () => {
    render(<JudgePanel judges={JUDGES} />);
    // D1's reasoning is collapsed initially
    expect(screen.queryByText("Facts verified.")).not.toBeInTheDocument();
    const d1Button = screen.getAllByRole("button").find((b) => b.textContent?.includes("D1"));
    expect(d1Button).toBeDefined();
    fireEvent.click(d1Button!);
    expect(screen.getByText("Facts verified.")).toBeInTheDocument();
  });

  it("surfaces an overall verdict + reasoning", () => {
    render(
      <JudgePanel judges={JUDGES} verdict="PASS" reasoning="All hard gates cleared." />,
    );
    expect(screen.getByText(/Verdict · PASS/)).toBeInTheDocument();
    expect(screen.getByText("All hard gates cleared.")).toBeInTheDocument();
  });
});
