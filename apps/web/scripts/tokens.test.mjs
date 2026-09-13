import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { render, shellColors, shellMetrics, shellType } from "./tokens.mjs";

const DESIGN = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "..", "design");
const shell = readFileSync(join(DESIGN, "SHELL.md"), "utf8");
const design = readFileSync(join(DESIGN, "DESIGN.md"), "utf8");

describe("design tokens", () => {
  it("reads the SHELL.md colour list, including the hairline note", () => {
    const c = shellColors(shell);
    expect(c.primary).toBe("#006194");
    expect(c["success-tint"]).toBe("#d7f2e0");
    expect(c["warning-tint"]).toBe("#fff1cc");
    expect(c.secondary).toBe("#4648d4");
    expect(c.hairline).toBe("#dbe3ee");
    expect(c).not.toHaveProperty("use");
    expect(c).not.toHaveProperty("solid");
  });

  it("reads the type ramp and metrics", () => {
    const t = shellType(shell);
    expect(t.title).toEqual({ size: "24px", weight: "600", tracking: "-0.02em", lineHeight: null });
    expect(t.body.lineHeight).toBe("20px");
    expect(t.label.tracking).toBe(".06em");
    const m = shellMetrics(shell);
    expect(m.radius).toEqual({ card: "10px", control: "8px", chip: "999px" });
    expect(m.spacing.control).toBe("36px");
  });

  it("committed tokens.css matches the design files", () => {
    const committed = readFileSync(join(dirname(fileURLToPath(import.meta.url)), "..", "src", "styles", "tokens.css"), "utf8");
    expect(committed.replace(/\r\n/g, "\n")).toBe(render(design, shell));
  });
});
