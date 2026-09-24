import { describe, expect, it } from "vitest"

import { parseDiffHunk } from "./diffHunk"

const HUNK = [
  "@@ -41,6 +41,9 @@ export async function refresh(token: string) {",
  " const a = 1",
  "-  const next = await fetchToken(token)",
  "+  const next = await fetchToken(token, { skewSeconds: 30 })",
  '+  if (!next) throw new TokenError("refresh returned nothing")',
  "   return next",
].join("\n")

describe("parseDiffHunk", () => {
  it("numbers both sides from the header, advancing each side by row kind", () => {
    const { header, lines } = parseDiffHunk(HUNK)

    expect(header).toBe(
      "@@ -41,6 +41,9 @@ export async function refresh(token: string) {",
    )
    expect(lines).toEqual([
      { oldLine: 41, newLine: 41, kind: "context", text: "const a = 1" },
      {
        oldLine: 42,
        newLine: null,
        kind: "removed",
        text: "  const next = await fetchToken(token)",
      },
      {
        oldLine: null,
        newLine: 42,
        kind: "added",
        text: "  const next = await fetchToken(token, { skewSeconds: 30 })",
      },
      {
        oldLine: null,
        newLine: 43,
        kind: "added",
        text: '  if (!next) throw new TokenError("refresh returned nothing")',
      },
      { oldLine: 43, newLine: 44, kind: "context", text: "  return next" },
    ])
  })

  it("does not let the end-of-file marker take a line number", () => {
    const hunk = [
      "@@ -7,1 +7,1 @@",
      "-  return 1",
      "+  return 2",
      "\\ No newline at end of file",
    ].join("\n")

    const { lines } = parseDiffHunk(hunk)

    expect(lines).toHaveLength(2)
    expect(lines[1]).toEqual({
      oldLine: null,
      newLine: 7,
      kind: "added",
      text: "  return 2",
    })
  })

  it("reads a header that omits the line counts", () => {
    const { header, lines } = parseDiffHunk(
      ["@@ -12 +12 @@", "-old", "+new"].join("\n"),
    )

    expect(header).toBe("@@ -12 +12 @@")
    expect(lines).toEqual([
      { oldLine: 12, newLine: null, kind: "removed", text: "old" },
      { oldLine: null, newLine: 12, kind: "added", text: "new" },
    ])
  })
})
