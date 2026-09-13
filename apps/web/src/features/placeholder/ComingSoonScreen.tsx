import type { NavItem } from "@/features/shell/nav"

export interface ComingSoonScreenProps {
  item: NavItem
}

export function ComingSoonScreen({ item }: ComingSoonScreenProps) {
  return (
    <div className="mx-auto flex w-full max-w-[1500px] animate-fade-up flex-col gap-6 p-6">
      <header className="flex flex-col gap-1.5">
        <p className="font-mono text-2xs uppercase tracking-widest text-muted-foreground">
          {item.group}
        </p>
        <h1 className="text-xl font-semibold tracking-tight">{item.label}</h1>
        <p className="max-w-2xl text-sm text-muted-foreground">
          {item.description}
        </p>
      </header>

      <div className="flex flex-col items-center gap-4 rounded-xl border border-dashed border-border bg-card/60 px-6 py-20 text-center">
        <span className="grid size-12 place-items-center rounded-full border border-border bg-muted text-muted-foreground">
          <item.icon className="size-5" />
        </span>
        <div className="flex flex-col gap-1">
          <h2 className="text-base font-semibold tracking-tight">
            {item.label} is next
          </h2>
          <p className="max-w-md text-sm text-muted-foreground">
            This screen is designed on the same shell and API contract. It is
            part of the interactive design loop and arrives in a later pass.
          </p>
        </div>
        <span className="rounded-full border border-border bg-background px-3 py-1 font-mono text-2xs uppercase tracking-widest text-muted-foreground">
          Not built yet
        </span>
      </div>
    </div>
  )
}
