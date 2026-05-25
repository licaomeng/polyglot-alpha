import { cn, shortAddr, formatUsd, formatNumber } from "@/lib/utils";

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
});
