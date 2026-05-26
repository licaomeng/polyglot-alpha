import {
  cn,
  shortAddr,
  formatUsd,
  formatNumber,
  formatWinsBids,
  isSimTxHash,
  isSimPolymarketId,
  arcscanTxUrl,
  polymarketMarketUrl,
  safePolymarketUrl,
} from "@/lib/utils";

describe("utils", () => {
  it("cn merges tailwind classes and de-duplicates conflicts", () => {
    expect(cn("p-2", "p-4")).toBe("p-4");
  });

  it("shortAddr produces a head…tail abbreviation", () => {
    expect(shortAddr("0xAABBCCDDEEFF11223344", 4, 4)).toBe("0xAA…3344");
  });

  it("shortAddr returns em dash for empty input", () => {
    expect(shortAddr(null)).toBe("—");
  });

  it("formatUsd formats numbers as US currency", () => {
    expect(formatUsd(12.3)).toMatch(/\$12\.30/);
  });

  it("formatNumber handles null gracefully", () => {
    expect(formatNumber(null)).toBe("—");
  });

  describe("formatWinsBids (W14-D)", () => {
    it("renders wins/total · pct for valid inputs", () => {
      expect(formatWinsBids(12, 47)).toBe("12/47 · 26%");
    });

    it("rounds the percent to the nearest integer", () => {
      // 7/9 = 77.77…% → "78%"
      expect(formatWinsBids(7, 9)).toBe("7/9 · 78%");
    });

    it("omits the percent when no bids have been entered", () => {
      expect(formatWinsBids(0, 0)).toBe("0/0");
    });

    it("returns an em-dash when either side is missing", () => {
      expect(formatWinsBids(null, 10)).toBe("—");
      expect(formatWinsBids(3, undefined)).toBe("—");
      expect(formatWinsBids(undefined, undefined)).toBe("—");
      expect(formatWinsBids(Number.NaN, 5)).toBe("—");
    });
  });

  describe("sim-prefix gating (W7-B)", () => {
    it("isSimTxHash detects 0xsim_ prefix (case-insensitive)", () => {
      expect(isSimTxHash("0xsim_abcdef")).toBe(true);
      expect(isSimTxHash("0xSIM_ABCDEF")).toBe(true);
    });

    it("isSimTxHash rejects real 0x… hashes and null", () => {
      expect(isSimTxHash("0xabcdef0123456789")).toBe(false);
      expect(isSimTxHash(null)).toBe(false);
      expect(isSimTxHash(undefined)).toBe(false);
      expect(isSimTxHash("")).toBe(false);
    });

    it("isSimPolymarketId detects sim- and dryrun- prefixes", () => {
      expect(isSimPolymarketId("sim-ff89e42a7a8d")).toBe(true);
      expect(isSimPolymarketId("dryrun-abcdef")).toBe(true);
      expect(isSimPolymarketId("SIM-foo")).toBe(true);
    });

    it("isSimPolymarketId rejects real market ids and falsy values", () => {
      expect(isSimPolymarketId("0x123abc")).toBe(false);
      expect(isSimPolymarketId("real-market-42")).toBe(false);
      expect(isSimPolymarketId(null)).toBe(false);
      expect(isSimPolymarketId(undefined)).toBe(false);
    });

    it("arcscanTxUrl returns null for sim hashes and a URL for real hashes", () => {
      expect(arcscanTxUrl("0xsim_abcdef")).toBeNull();
      expect(arcscanTxUrl(null)).toBeNull();
      expect(arcscanTxUrl("0xabcdef")).toBe("https://testnet.arcscan.app/tx/0xabcdef");
    });

    it("polymarketMarketUrl returns null for sim/dryrun ids and a URL for real ids", () => {
      expect(polymarketMarketUrl("sim-ff89e42a7a8d")).toBeNull();
      expect(polymarketMarketUrl("dryrun-abc")).toBeNull();
      expect(polymarketMarketUrl(null)).toBeNull();
      expect(polymarketMarketUrl("0xdeadbeef")).toBe(
        "https://polymarket.com/market/0xdeadbeef",
      );
    });

    it("safePolymarketUrl strips URLs whose market_id segment is sim-prefixed", () => {
      expect(safePolymarketUrl("https://polymarket.com/market/sim-ff89e4")).toBeNull();
      expect(safePolymarketUrl("https://polymarket.com/market/dryrun-abc")).toBeNull();
      expect(safePolymarketUrl(null)).toBeNull();
      expect(safePolymarketUrl("")).toBeNull();
      expect(safePolymarketUrl("https://polymarket.com/market/0xdeadbeef")).toBe(
        "https://polymarket.com/market/0xdeadbeef",
      );
    });
  });
});
