import { useCallback, useEffect, useRef, useState } from "react"

import type { ReviewAttachment } from "@/api/contract"

export interface AttachmentItem {
  attachment: ReviewAttachment
  previewUrl: string
}

function createId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID()
  }
  return `att_${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`
}

export function formatBytes(size: number): string {
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(0)} KB`
  return `${(size / (1024 * 1024)).toFixed(1)} MB`
}

export function filesFromClipboard(data: DataTransfer | null): File[] {
  if (!data) return []
  const fromItems = Array.from(data.items)
    .filter((item) => item.kind === "file")
    .map((item) => item.getAsFile())
    .filter((file): file is File => file !== null)
  if (fromItems.length > 0) return fromItems
  return Array.from(data.files)
}

export function useAttachments() {
  const [items, setItems] = useState<AttachmentItem[]>([])
  const itemsRef = useRef<AttachmentItem[]>([])
  const objectUrls = useRef<Set<string>>(new Set())

  const commit = useCallback((next: AttachmentItem[]) => {
    itemsRef.current = next
    setItems(next)
  }, [])

  const addFiles = useCallback(
    (files: FileList | File[] | null): number => {
      if (!files) return 0
      const list = Array.from(files)
      if (list.length === 0) return 0

      const added = list.map((file) => {
        const previewUrl = URL.createObjectURL(file)
        objectUrls.current.add(previewUrl)
        return {
          previewUrl,
          attachment: {
            id: createId(),
            name: file.name || "file",
            mime: file.type || "application/octet-stream",
            size: file.size,
          },
        } satisfies AttachmentItem
      })
      commit([...itemsRef.current, ...added])
      return added.length
    },
    [commit],
  )

  const remove = useCallback(
    (id: string) => {
      const target = itemsRef.current.find((item) => item.attachment.id === id)
      if (target) {
        URL.revokeObjectURL(target.previewUrl)
        objectUrls.current.delete(target.previewUrl)
      }
      commit(itemsRef.current.filter((item) => item.attachment.id !== id))
    },
    [commit],
  )

  const clear = useCallback(() => {
    itemsRef.current.forEach((item) => {
      URL.revokeObjectURL(item.previewUrl)
      objectUrls.current.delete(item.previewUrl)
    })
    commit([])
  }, [commit])

  useEffect(() => {
    const urls = objectUrls.current
    return () => {
      urls.forEach((url) => URL.revokeObjectURL(url))
      urls.clear()
    }
  }, [])

  return {
    items,
    metadata: items.map((item) => item.attachment),
    addFiles,
    remove,
    clear,
  }
}
