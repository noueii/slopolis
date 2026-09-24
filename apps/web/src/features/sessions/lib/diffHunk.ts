/**
 * The unified-diff hunk GitHub prints above a review comment, split into the
 * rows the finding card renders.
 *
 * The wire carries the hunk as literal text (`diffHunk`), not as a structure,
 * so the card numbers its own lines: the `@@` header gives the starting old
 * and new line numbers, and every row advances them — a `+` row only the new
 * one, a `-` row only the old one, a context row both. That numbering is what
 * marks the row the comment is anchored to.
 */

/** How one hunk row changed the file. */
export type DiffHunkLineKind = "context" | "added" | "removed"

export interface DiffHunkLine {
  /** Line number before the change, or `null` for an added line. */
  oldLine: number | null
  /** Line number after the change, or `null` for a removed line. */
  newLine: number | null
  kind: DiffHunkLineKind
  /** The line's text, without its `+`/`-`/space marker. */
  text: string
}

export interface DiffHunk {
  /** The `@@ -a,b +c,d @@` line, rendered as the hunk's heading. */
  header: string
  lines: DiffHunkLine[]
}

/** GitHub's hunk header; the counts are optional in the unified-diff grammar. */
const HEADER = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/

/**
 * GitHub's hunk text always opens with the header, so the first line is read
 * as one even when its numbers do not parse — the rows then number from 1
 * rather than pretending to a position the header never stated.
 */
export function parseDiffHunk(hunk: string): DiffHunk {
  const [header = "", ...body] = hunk.split("\n")
  const start = HEADER.exec(header)
  let oldLine = start ? Number(start[1]) : 1
  let newLine = start ? Number(start[2]) : 1

  const lines: DiffHunkLine[] = []
  for (const raw of body) {
    // `\ No newline at end of file` describes the line before it; it is not a
    // line of its own, so it neither renders nor advances the numbering.
    if (raw.startsWith("\\")) continue
    const text = raw.slice(1)
    if (raw.startsWith("+")) {
      lines.push({ oldLine: null, newLine: newLine++, kind: "added", text })
    } else if (raw.startsWith("-")) {
      lines.push({ oldLine: oldLine++, newLine: null, kind: "removed", text })
    } else {
      lines.push({
        oldLine: oldLine++,
        newLine: newLine++,
        kind: "context",
        text,
      })
    }
  }

  return { header, lines }
}
