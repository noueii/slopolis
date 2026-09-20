import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import type { RepositorySummary } from "@/api/contract"

export interface RepositoryAccessDialogProps {
  repository: RepositorySummary
  pending: boolean
  error: string | null
  onOpenChange: (open: boolean) => void
  onConfirm: () => void
}

/**
 * Disabling is reversible but has a consequence the caller cannot see from the
 * toggle alone, so it is confirmed with the consequence spelled out: pre-flight
 * refuses the repository's pull requests until it is enabled again.
 */
export function RepositoryAccessDialog({
  repository,
  pending,
  error,
  onOpenChange,
  onConfirm,
}: RepositoryAccessDialogProps) {
  return (
    <Dialog open onOpenChange={onOpenChange}>
      <DialogContent className="max-w-[440px]">
        <DialogHeader>
          <DialogTitle>Disable {repository.fullName}?</DialogTitle>
          <DialogDescription>
            slopolis stops reviewing this repository: pre-flight refuses its pull
            requests with the reason named, so no new review can start from it.
            Its sessions and findings stay, and you can enable it again at any
            time.
          </DialogDescription>
        </DialogHeader>

        {error !== null ? (
          <p role="alert" className="text-xs leading-relaxed text-destructive">
            {error}
          </p>
        ) : null}

        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={pending}
            onClick={() => onOpenChange(false)}
          >
            Cancel
          </Button>
          <Button
            type="button"
            size="sm"
            disabled={pending}
            onClick={onConfirm}
          >
            {pending ? "Disabling…" : "Disable repository"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
