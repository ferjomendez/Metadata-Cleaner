import { describe, expect, it } from "vitest";
import { clockSkew, formatBytes, formatCountdown, splitKey } from "./format";
import { matchRoute } from "./router";

describe("formatBytes", () => {
  it("uses binary units", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(1536)).toMatch(/^1[.,]5 KB$/);
    expect(formatBytes(500 * 1024 * 1024)).toBe("500 MB");
    expect(formatBytes(2 * 1024 ** 3)).toBe("2 GB");
    expect(formatBytes(null)).toBe("–");
  });
});

describe("formatCountdown", () => {
  it("formats minutes and hours", () => {
    expect(formatCountdown(0)).toBe("00:00");
    expect(formatCountdown(61)).toBe("01:01");
    expect(formatCountdown(3599)).toBe("59:59");
    expect(formatCountdown(3661)).toBe("1:01:01");
    expect(formatCountdown(-5)).toBe("00:00");
  });
});

describe("clockSkew", () => {
  it("is zero for invalid input and positive when the server is ahead", () => {
    expect(clockSkew("nonsense")).toBe(0);
    const ahead = new Date(Date.now() + 60_000).toISOString();
    expect(clockSkew(ahead)).toBeGreaterThan(55_000);
  });
});

describe("splitKey", () => {
  it("splits group and tag", () => {
    expect(splitKey("IFD0:Artist")).toEqual({ group: "IFD0", tag: "Artist" });
    expect(splitKey("Plain")).toEqual({ group: "", tag: "Plain" });
  });
});

describe("router", () => {
  it("matches only well formed job ids", () => {
    const id = "a".repeat(32);
    expect(matchRoute(`/trabajos/${id}`)).toEqual({ name: "job", id });
    expect(matchRoute("/trabajos/../../etc")).toEqual({ name: "notFound" });
    expect(matchRoute("/")).toEqual({ name: "clean" });
    expect(matchRoute("/admin")).toEqual({ name: "admin" });
  });
});
