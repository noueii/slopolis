/** Client-side parsing of pasted GitHub pull request links. */

export interface ParsedPrUrl {
  fullName: string
  number: number
  url: string
}

const PR_URL_PATTERN =
  /^https?:\/\/github\.com\/([^/\s]+)\/([^/\s]+)\/pull\/(\d+)/i

export function parsePrUrl(raw: string): ParsedPrUrl | null {
  const match = raw.trim().match(PR_URL_PATTERN)
  if (!match) return null
  const fullName = `${match[1]}/${match[2]}`
  const number = Number(match[3])
  return {
    fullName,
    number,
    url: `https://github.com/${fullName}/pull/${number}`,
  }
}

export function prLabel(parsed: ParsedPrUrl): string {
  return `${parsed.fullName}#${parsed.number}`
}

export interface ParsedPrLinks {
  links: ParsedPrUrl[]
  invalid: string[]
}

/** Splits a pasted blob on whitespace/commas and keeps the parseable links. */
export function parsePrLinks(input: string): ParsedPrLinks {
  const links: ParsedPrUrl[] = []
  const invalid: string[] = []
  const seen = new Set<string>()

  for (const token of input.split(/[\s,]+/)) {
    const value = token.trim()
    if (!value) continue
    const parsed = parsePrUrl(value)
    if (!parsed) {
      invalid.push(value)
      continue
    }
    if (seen.has(parsed.url)) continue
    seen.add(parsed.url)
    links.push(parsed)
  }

  return { links, invalid }
}
