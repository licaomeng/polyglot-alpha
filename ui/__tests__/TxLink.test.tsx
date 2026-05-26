import { render, screen } from "@testing-library/react";
import { TxLink } from "@/components/onchain/TxLink";

describe("TxLink", () => {
  it("renders an Arc explorer link with the abbreviated tx hash", () => {
    render(<TxLink txHash="0xabcdef0123456789abcdef" />);
    const link = screen.getByRole("link");
    expect(link).toHaveAttribute("href", expect.stringContaining("arcscan.app/tx/0xabcdef"));
  });

  it("tags the link with a Live badge by default", () => {
    render(<TxLink txHash="0xabc" />);
    expect(screen.getByText("Live")).toBeInTheDocument();
  });

  it("can render a Mock badge for dry-run/simulated TXs", () => {
    render(<TxLink txHash="0xabc" mode="mock" />);
    expect(screen.getByText("Mock")).toBeInTheDocument();
  });

  it("does NOT wrap a 0xsim_ hash in an external link", () => {
    // W7-B: synthetic mock tx hashes must render as muted text, not as a
    // clickable link to https://testnet.arcscan.app/tx/0xsim_… (which 404s).
    render(<TxLink txHash="0xsim_abcdef0123456789" mode="mock" />);
    expect(screen.queryByRole("link")).toBeNull();
    expect(
      screen.getByLabelText(/Synthetic transaction .* \(not on-chain\)/i),
    ).toBeInTheDocument();
  });
});
