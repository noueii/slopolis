/** Pure helpers for selecting and grouping pull requests. */

import type {
  OpenPullRequest,
  PrReference,
  RepositoryRef,
} from "@/api/contract"

/** The UI treats a picker selection and a pasted link as the same thing. */
export type SelectedPr = PrReference

export function toSelectedPr(pr: OpenPullRequest): SelectedPr {
  return {
    url: pr.url,
    repository: pr.repository,
    number: pr.number,
    title: pr.title,
  }
}

export function selectionKey(item: {
  repository: RepositoryRef
  number: number
}): string {
  return `${item.repository.fullName}#${item.number}`
}

export function ownerOf(fullName: string): string {
  return fullName.split("/")[0] ?? fullName
}

export function nameOf(fullName: string): string {
  return fullName.split("/").slice(1).join("/") || fullName
}

/** Two-letter monogram for a repository mark (`api-gateway` → `AG`). */
export function repoMonogram(fullName: string): string {
  const name = nameOf(fullName)
  const parts = name.split(/[-_.\s]+/).filter(Boolean)
  if (parts.length >= 2) {
    return `${parts[0][0]}${parts[1][0]}`.toUpperCase()
  }
  return name.slice(0, 2).toUpperCase()
}

export interface SelectedGroup {
  repository: RepositoryRef
  items: SelectedPr[]
}

/** Groups selected PRs by repository, preserving first-seen order. */
export function groupSelectedByRepository(
  items: SelectedPr[],
): SelectedGroup[] {
  const groups = new Map<string, SelectedGroup>()
  for (const item of items) {
    const key = item.repository.fullName
    const existing = groups.get(key)
    if (existing) {
      existing.items.push(item)
    } else {
      groups.set(key, { repository: item.repository, items: [item] })
    }
  }
  return [...groups.values()]
}
