import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from "react"
import {
  AlertTriangle,
  ArrowRight,
  Check,
  Info,
  Link2,
  ListChecks,
  Loader2,
  Plus,
  RotateCw,
  Sparkles,
  X,
} from "lucide-react"

import { cn } from "@/lib/utils"
import { ApiError, api } from "@/api/client"
import type {
  CreatedSession,
  ModelCatalog,
  PreflightResult,
} from "@/api/contract"
import { Button } from "@/components/ui/button"
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectSeparator,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"
import { SessionStatusBadge } from "@/features/sessions/components/SessionStatusBadge"
import { formatRelativeTime } from "@/features/sessions/lib/format"
import { parsePrLinks } from "./lib/prUrl"
import {
  groupSelectedByRepository,
  type SelectedPr,
} from "./lib/selection"
import { RepositoryMark } from "./components/RepositoryMark"

export interface NewReviewComposerHandle {
  focus: () => void
}

export interface NewReviewComposerProps {
  selected: SelectedPr[]
  prompt: string
  onPromptChange: (value: string) => void
  onRemove: (url: string) => void
  onAdd: (pr: SelectedPr) => void
  onClear: () => void
  onOpenPicker: () => void
  onOpenSession?: (id: string) => void
  autoFocus?: boolean
  model: string
  onModelChange: (value: string) => void
  catalog: ModelCatalog | null
  catalogStatus: "loading" | "success" | "error"
  onRetryCatalog?: () => void
}

type Phase = "idle" | "validating" | "creating" | "created"

export const NewReviewComposer = forwardRef<
  NewReviewComposerHandle,
  NewReviewComposerProps
>(function NewReviewComposer(
  {
    selected,
    prompt,
    onPromptChange,
    onRemove,
    onAdd,
    onClear,
    onOpenPicker,
    onOpenSession,
    autoFocus = false,
    model,
    onModelChange,
    catalog,
    catalogStatus,
    onRetryCatalog,
  },
  ref,
) {
  const [pasteOpen, setPasteOpen] = useState(false)
  const [pasteInput, setPasteInput] = useState("")
  const [pasteBusy, setPasteBusy] = useState(false)
  const [phase, setPhase] = useState<Phase>("idle")
  const [error, setError] = useState<string | null>(null)
  const [notices, setNotices] = useState<string[]>([])
  const [created, setCreated] = useState<CreatedSession | null>(null)

  const textareaRef = useRef<HTMLTextAreaElement>(null)

  useImperativeHandle(ref, () => ({
    focus: () => textareaRef.current?.focus(),
  }))

  useEffect(() => {
    if (autoFocus) textareaRef.current?.focus()
  }, [autoFocus])

  const groups = useMemo(
    () => groupSelectedByRepository(selected),
    [selected],
  )
  const repoCount = groups.length
  const busy = phase === "validating" || phase === "creating"

  const reset = () => {
    onPromptChange("")
    setPasteInput("")
    setPasteOpen(false)
    setPhase("idle")
    setError(null)
    setNotices([])
    setCreated(null)
    onClear()
    window.setTimeout(() => textareaRef.current?.focus(), 0)
  }

  const handleAddPasted = async () => {
    const { links, invalid } = parsePrLinks(pasteInput)
    if (links.length === 0) {
      setError(
        invalid.length > 0
          ? `“${invalid[0]}” is not a GitHub pull request link.`
          : "Paste a GitHub pull request link.",
      )
      return
    }
    setError(null)
    setPasteBusy(true)
    try {
      const result = await api.preflightReview({
        prUrls: links.map((link) => link.url),
      })
      if (result.valid.length === 0) {
        setError(
          "None of these links belong to a repository covered by this workspace.",
        )
        return
      }
      result.valid.forEach(onAdd)
      setNotices(result.notices)
      setPasteInput("")
      setPasteOpen(false)
    } catch (cause) {
      setError(
        cause instanceof ApiError
          ? cause.message
          : "Could not resolve that link.",
      )
    } finally {
      setPasteBusy(false)
    }
  }

  const submit = async () => {
    if (selected.length === 0) {
      setError("Choose at least one pull request to review.")
      onOpenPicker()
      return
    }

    setError(null)
    setNotices([])
    setPhase("validating")

    try {
      const result: PreflightResult = await api.preflightReview({
        prUrls: selected.map((item) => item.url),
      })
      if (result.valid.length === 0) {
        setNotices(result.notices)
        setError(
          "None of these pull requests belong to a repository covered by this workspace.",
        )
        setPhase("idle")
        return
      }

      setNotices(result.notices)
      setPhase("creating")
      const session = await api.createReviewSession({
        prUrls: result.valid.map((item) => item.url),
        prompt: prompt.trim() || undefined,
        model,
      })
      setCreated(session)
      setPhase("created")
    } catch (cause) {
      setError(
        cause instanceof ApiError
          ? cause.message
          : "Something went wrong while starting the review.",
      )
      setPhase("idle")
    }
  }

  const handlePromptKeyDown = (
    event: React.KeyboardEvent<HTMLTextAreaElement>,
  ) => {
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      event.preventDefault()
      void submit()
    }
  }

  if (phase === "created" && created) {
    return (
      <CreatedPanel
        session={created}
        onOpenSession={onOpenSession}
        onReset={reset}
      />
    )
  }

  return (
    <div className="overflow-hidden rounded-2xl border border-border bg-card shadow-[0_1px_2px_hsl(var(--foreground)/0.04),0_12px_32px_-16px_hsl(var(--foreground)/0.18)]">
      <div className="flex items-center justify-between gap-3 px-5 pb-1 pt-4">
        <div className="flex items-center gap-2">
          <span className="grid size-6 place-items-center rounded-md bg-accent/10 text-accent">
            <Sparkles className="size-3.5" />
          </span>
          <span className="text-[13px] font-medium text-foreground">
            Start a new review
          </span>
        </div>
        <ModelSelect
          model={model}
          onModelChange={onModelChange}
          catalog={catalog}
          status={catalogStatus}
          onRetry={onRetryCatalog}
        />
      </div>

      <div className="px-5 pt-1">
        <Textarea
          ref={textareaRef}
          value={prompt}
          onChange={(event) => onPromptChange(event.target.value)}
          onKeyDown={handlePromptKeyDown}
          rows={3}
          placeholder="Describe what this review should focus on…"
          aria-label="Review focus"
          className="min-h-[88px] resize-none border-0 bg-transparent px-0 py-1 text-[15px] leading-relaxed shadow-none focus-visible:ring-0"
        />
      </div>

      <div className="px-5 pb-4">
        {selected.length === 0 ? (
          <button
            type="button"
            onClick={onOpenPicker}
            className="flex w-full items-center gap-2.5 rounded-xl border border-dashed border-border bg-muted/20 px-3.5 py-3 text-left transition-colors hover:border-accent/40 hover:bg-accent/[0.04]"
          >
            <span className="grid size-7 shrink-0 place-items-center rounded-lg border border-border bg-background text-muted-foreground">
              <ListChecks className="size-3.5" />
            </span>
            <span className="flex min-w-0 flex-col">
              <span className="text-[13px] font-medium text-foreground">
                Choose pull requests
              </span>
              <span className="truncate text-2xs text-muted-foreground">
                Pick open PRs across your connected repositories.
              </span>
            </span>
            <ArrowRight className="ml-auto size-4 shrink-0 text-muted-foreground" />
          </button>
        ) : (
          <div className="flex flex-col gap-2.5">
            {groups.map((group) => (
              <div
                key={group.repository.fullName}
                className="flex flex-col gap-1.5"
              >
                <div className="flex items-center gap-2">
                  <RepositoryMark
                    fullName={group.repository.fullName}
                    private={group.repository.private}
                    size="sm"
                  />
                  <span className="truncate font-mono text-[11px] font-medium text-muted-foreground">
                    {group.repository.fullName}
                  </span>
                  <span className="font-mono text-[10px] text-muted-foreground/70">
                    {group.items.length}
                  </span>
                </div>
                <div className="flex flex-wrap gap-1.5 pl-0.5">
                  {group.items.map((item) => (
                    <span
                      key={item.url}
                      className="group/chip inline-flex max-w-full items-center gap-1.5 rounded-full border border-border bg-muted/50 py-1 pl-2.5 pr-1"
                    >
                      <a
                        href={item.url}
                        target="_blank"
                        rel="noreferrer"
                        className="shrink-0 font-mono text-[11px] font-medium text-foreground/85 hover:text-accent"
                      >
                        #{item.number}
                      </a>
                      <span className="max-w-[190px] truncate text-[11px] text-muted-foreground">
                        {item.title}
                      </span>
                      <button
                        type="button"
                        onClick={() => onRemove(item.url)}
                        aria-label={`Remove pull request #${item.number}`}
                        className="grid size-4 shrink-0 place-items-center rounded-full text-muted-foreground transition-colors hover:bg-background hover:text-foreground"
                      >
                        <X className="size-3" />
                      </button>
                    </span>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {error ? (
        <div className="mx-5 mb-3 flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2">
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-destructive" />
          <p className="text-[12px] leading-relaxed text-destructive">{error}</p>
        </div>
      ) : null}

      {!error && notices.length > 0 ? (
        <div className="mx-5 mb-3 flex flex-col gap-1 rounded-md border border-border bg-muted/40 px-3 py-2">
          {notices.map((notice) => (
            <p
              key={notice}
              className="flex items-start gap-2 text-[12px] leading-relaxed text-muted-foreground"
            >
              <Info className="mt-0.5 size-3.5 shrink-0" />
              {notice}
            </p>
          ))}
        </div>
      ) : null}

      <Collapsible open={pasteOpen} onOpenChange={setPasteOpen}>
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border/70 bg-muted/30 px-5 py-3">
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="outline" size="sm" onClick={onOpenPicker}>
              <ListChecks data-icon="inline-start" />
              {selected.length > 0 ? "Edit selection" : "Choose pull requests"}
              {selected.length > 0 ? (
                <span className="ml-0.5 rounded-full bg-accent/12 px-1.5 font-mono text-[10px] font-semibold text-accent">
                  {selected.length}
                </span>
              ) : null}
            </Button>
            <CollapsibleTrigger asChild>
              <Button
                variant="ghost"
                size="sm"
                className="text-muted-foreground"
              >
                <Link2 data-icon="inline-start" />
                Paste a link
              </Button>
            </CollapsibleTrigger>
          </div>
          <Button
            size="lg"
            className="h-9 gap-2"
            onClick={() => void submit()}
            disabled={busy || selected.length === 0}
          >
            {busy ? (
              <Loader2 data-icon="inline-start" className="animate-spin" />
            ) : null}
            {phase === "validating"
              ? "Checking…"
              : phase === "creating"
                ? "Starting…"
                : "Start review"}
            {!busy ? <ArrowRight data-icon="inline-end" /> : null}
          </Button>
        </div>

        <CollapsibleContent>
          <div className="flex flex-col gap-2 border-t border-border/70 bg-muted/20 px-5 py-3">
            <div className="flex flex-wrap items-center gap-2">
              <div className="flex items-center gap-1.5 rounded-full border border-dashed border-border bg-background px-2.5 py-1">
                <Link2 className="size-3.5 shrink-0 text-muted-foreground" />
                <Input
                  value={pasteInput}
                  onChange={(event) => setPasteInput(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") {
                      event.preventDefault()
                      void handleAddPasted()
                    }
                  }}
                  aria-label="Add pull request link"
                  placeholder="https://github.com/owner/repo/pull/123"
                  className="h-6 w-[280px] border-0 bg-transparent p-0 font-mono text-xs shadow-none focus-visible:ring-0"
                />
                <button
                  type="button"
                  onClick={() => void handleAddPasted()}
                  disabled={!pasteInput.trim() || pasteBusy}
                  aria-label="Add pull request link"
                  className="grid size-5 place-items-center rounded-full text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-40"
                >
                  {pasteBusy ? (
                    <Loader2 className="size-3.5 animate-spin" />
                  ) : (
                    <Plus className="size-3.5" />
                  )}
                </button>
              </div>
              <p className="text-2xs text-muted-foreground">
                Pasting is a shortcut; selecting from the list is the primary
                way in.
              </p>
            </div>
          </div>
        </CollapsibleContent>
      </Collapsible>

      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border/70 px-5 py-2.5">
        <p className="max-w-[520px] text-2xs leading-relaxed text-muted-foreground">
          {selected.length > 0
            ? `${selected.length} PR${selected.length === 1 ? "" : "s"} across ${repoCount} repositor${repoCount === 1 ? "y" : "ies"}. Sessions are auto-named from their targets.`
            : "Cross-repo reviews are supported. Sessions are auto-named from their targets."}
        </p>
        <span className="font-mono text-[10px] text-muted-foreground/70">
          ⌘↵ to start
        </span>
      </div>
    </div>
  )
})

interface ModelSelectProps {
  model: string
  onModelChange: (value: string) => void
  catalog: ModelCatalog | null
  status: "loading" | "success" | "error"
  onRetry?: () => void
}

function ModelSelect({
  model,
  onModelChange,
  catalog,
  status,
  onRetry,
}: ModelSelectProps) {
  const pill =
    "inline-flex h-8 items-center gap-1.5 rounded-full border border-border bg-muted/40 px-3 font-mono text-[11px] text-muted-foreground"

  if (status === "loading") {
    return (
      <span className={pill} aria-live="polite">
        <Loader2 className="size-3 animate-spin" />
        <span className="hidden sm:inline">Loading models…</span>
        <span className="sm:hidden">Models…</span>
      </span>
    )
  }

  if (status === "error") {
    return (
      <button
        type="button"
        onClick={onRetry}
        className={cn(pill, "transition-colors hover:text-foreground")}
        aria-label="Models unavailable, retry"
      >
        <AlertTriangle className="size-3 text-destructive" />
        <span className="hidden sm:inline">Models unavailable · retry</span>
        <span className="sm:hidden">Retry</span>
      </button>
    )
  }

  const options = catalog?.models ?? []
  const defaultLabel = catalog
    ? `auto · ${catalog.defaultModelId}`
    : "auto"

  return (
    <Select value={model} onValueChange={onModelChange}>
      <SelectTrigger
        aria-label="Review model"
        className="h-8 w-auto max-w-[230px] gap-1.5 rounded-full border-border bg-muted/40 px-3 py-0 font-mono text-[11px] text-muted-foreground shadow-none focus:ring-1 focus:ring-ring"
      >
        <SelectValue />
      </SelectTrigger>
      <SelectContent align="end" className="font-mono text-xs">
        <SelectItem value="auto" className="text-xs">
          {defaultLabel}
        </SelectItem>
        {options.length > 0 ? <SelectSeparator /> : null}
        {options.map((option) => (
          <SelectItem
            key={option.id}
            value={option.id}
            className="text-xs"
          >
            {option.id}{" "}
            <span className="text-muted-foreground">· {option.provider}</span>
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

interface CreatedPanelProps {
  session: CreatedSession
  onOpenSession?: (id: string) => void
  onReset: () => void
}

function CreatedPanel({ session, onOpenSession, onReset }: CreatedPanelProps) {
  return (
    <div className="animate-fade-up overflow-hidden rounded-2xl border border-border bg-card shadow-sm">
      <div className="flex items-center gap-3 px-5 pb-4 pt-5">
        <span className="grid size-9 shrink-0 place-items-center rounded-full bg-success/10 text-success">
          <Check className="size-4" />
        </span>
        <div className="flex min-w-0 flex-col">
          <span className="text-[14px] font-medium text-foreground">
            Review queued
          </span>
          <span className="truncate font-mono text-2xs text-muted-foreground">
            {session.id}
          </span>
        </div>
        <SessionStatusBadge status={session.status} className="ml-auto" />
      </div>

      <div className="grid grid-cols-2 gap-px overflow-hidden border-y border-border bg-border sm:grid-cols-4">
        <CreatedCell label="Session" value={session.name} />
        <CreatedCell
          label="Targets"
          value={`${session.targetCount} PR${session.targetCount === 1 ? "" : "s"}`}
        />
        <CreatedCell label="Model" value={session.model} />
        <CreatedCell
          label="Started"
          value={formatRelativeTime(session.createdAt)}
        />
      </div>

      {session.prompt ? (
        <p className="border-b border-border px-5 py-3 text-[13px] leading-relaxed text-muted-foreground">
          {session.prompt}
        </p>
      ) : null}

      <div className="flex flex-wrap items-center justify-between gap-3 px-5 py-3.5">
        <p className="text-2xs text-muted-foreground">
          Pre-flight passed. Findings publish to each pull request when the run
          completes.
        </p>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={onReset}>
            <RotateCw data-icon="inline-start" />
            Start another
          </Button>
          {onOpenSession ? (
            <Button size="sm" onClick={() => onOpenSession(session.id)}>
              View session
              <ArrowRight data-icon="inline-end" />
            </Button>
          ) : null}
        </div>
      </div>
    </div>
  )
}

function CreatedCell({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex min-w-0 flex-col gap-1 bg-card px-4 py-3">
      <span className="text-2xs font-semibold uppercase tracking-widest text-muted-foreground">
        {label}
      </span>
      <span className="truncate font-mono text-[12px] text-foreground">
        {value}
      </span>
    </div>
  )
}
