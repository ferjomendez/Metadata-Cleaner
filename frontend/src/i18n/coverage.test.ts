// Every code the backend can emit (errors, rejects, warnings, checks, "not
// verified" items) must have a Spanish text. This test reads the backend
// sources so a new code without a translation fails the build.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import esCL from "./es-CL";
import { interpolate, t } from "./index";

const backendDir = process.env.BACKEND_APP_DIR ?? join(__dirname, "..", "..", "..", "backend", "app");

function pySources(dir: string): string {
  return readdirSync(dir)
    .map((name) => join(dir, name))
    .map((p) => (statSync(p).isDirectory() ? pySources(p) : p.endsWith(".py") ? readFileSync(p, "utf-8") : ""))
    .join("\n");
}

const src = pySources(backendDir);
const all = (re: RegExp) => new Set(Array.from(src.matchAll(re), (m) => m[1]));

describe("i18n coverage of backend codes", () => {
  it("translates every policy warning", () => {
    for (const code of all(/"(w_[a-z0-9_]+)"/g)) expect(esCL.warnings[code], code).toBeTruthy();
  });

  it("translates every not-verified item", () => {
    const codes = all(/"(nv_[a-z0-9_]+)"/g);
    for (const kind of all(/"(windows_user_path|unix_home_path|email)"/g)) codes.add(`nv_possible_personal_${kind}`);
    for (const code of codes) {
      if (code === "nv_possible_personal_") continue;
      expect(esCL.notVerified[code], code).toBeTruthy();
    }
  });

  it("translates every verification check", () => {
    for (const code of all(/ctx\.check\(\s*"([a-z_]+)"/g)) expect(esCL.checks[code], code).toBeTruthy();
  });

  it("translates every error and reject code", () => {
    const codes = new Set<string>([
      ...all(/HTTPException\(\d+,\s*"([a-z_]+)"\)/g),
      ...all(/JobError\("([a-z_]+)"/g),
      ...all(/CleanError\("([a-z_]+)"/g),
      ...all(/ZipRejected\("([a-z_]+)"/g),
      ...all(/Detection\(None,\s*"([a-z_]+)"\)/g),
      ...all(/"error":\s*"([a-z_]+)"/g),
      ...all(/PasswordPolicyError\("([a-z_]+)"\)/g),
      ...all(/raise ValueError\("([a-z_]+)"\)/g),
      ...all(/fail_file\([^)]*"([a-z_]+)"\)/g),
      ...all(/error_code = '([a-z_]+)'/g),
    ]);
    codes.add("worker_lost");
    expect(codes.size).toBeGreaterThan(30);
    for (const code of codes) expect(esCL.errors[code], code).toBeTruthy();
  });
});

describe("t()", () => {
  it("interpolates parameters and falls back to the key", () => {
    expect(interpolate("Hola {name}", { name: "Ana" })).toBe("Hola Ana");
    expect(t("clean.fileStatus.uploading", { percent: 40 })).toBe("Subiendo 40%");
    expect(t("does.not.exist")).toBe("does.not.exist");
  });

  it("has no em dashes in user-facing text", () => {
    const text = JSON.stringify(esCL);
    expect(text.includes("—")).toBe(false);
  });
});
