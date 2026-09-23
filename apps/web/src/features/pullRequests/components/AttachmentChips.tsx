import { FileText, X } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import {
  formatBytes,
  type AttachmentItem,
} from "@/features/pullRequests/lib/useAttachments"

export interface AttachmentChipsProps {
  items: AttachmentItem[]
  onRemove: (id: string) => void
}

export function AttachmentChips({ items, onRemove }: AttachmentChipsProps) {
  if (items.length === 0) return null

  return (
    <ul
      aria-label="Attached files"
      className="flex flex-wrap gap-2"
    >
      {items.map(({ attachment, previewUrl }) => (
        <li
          key={attachment.id}
          className="flex max-w-full items-center gap-2 rounded-lg border border-border bg-muted/30 py-1 pl-1 pr-1.5"
        >
          {attachment.mime.startsWith("image/") ? (
            <img
              src={previewUrl}
              alt={attachment.name}
              className="size-8 shrink-0 rounded object-cover"
            />
          ) : (
            <span className="grid size-8 shrink-0 place-items-center rounded bg-muted text-muted-foreground">
              <FileText className="size-4" />
            </span>
          )}
          <span className="flex min-w-0 flex-col gap-0.5">
            <span className="max-w-[150px] truncate text-[11px] font-medium text-foreground">
              {attachment.name}
            </span>
            <Badge
              variant="secondary"
              className="w-fit rounded px-1 py-0 text-[10px] font-normal leading-4 text-muted-foreground"
            >
              {formatBytes(attachment.size)}
            </Badge>
          </span>
          <button
            type="button"
            onClick={() => onRemove(attachment.id)}
            aria-label={`Remove ${attachment.name}`}
            className="grid size-4 shrink-0 place-items-center rounded-full text-muted-foreground transition-colors hover:bg-background hover:text-foreground"
          >
            <X className="size-3" />
          </button>
        </li>
      ))}
    </ul>
  )
}
