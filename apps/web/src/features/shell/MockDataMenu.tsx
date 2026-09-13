import { ChevronDown, FlaskConical } from "lucide-react"

import { cn } from "@/lib/utils"
import type { MockScenario } from "@/api/client"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"

export interface MockDataMenuProps {
  scenario: MockScenario
  onScenarioChange: (scenario: MockScenario) => void
}

const SCENARIOS: Array<{
  value: MockScenario
  label: string
  description: string
}> = [
  {
    value: "default",
    label: "Default dataset",
    description: "57 realistic sessions across 10 repositories",
  },
  {
    value: "empty",
    label: "Empty dataset",
    description: "No sessions returned",
  },
  {
    value: "error",
    label: "Simulate error",
    description: "Requests fail with HTTP 500",
  },
  {
    value: "slow",
    label: "Slow network",
    description: "Adds ~2.2s of latency",
  },
]

export function MockDataMenu({ scenario, onScenarioChange }: MockDataMenuProps) {
  const active = SCENARIOS.find((item) => item.value === scenario) ?? SCENARIOS[0]
  const isDefault = scenario === "default"

  return (
    <DropdownMenu>
      <Tooltip>
        <TooltipTrigger asChild>
          <DropdownMenuTrigger asChild>
            <button
              type="button"
              className={cn(
                "inline-flex h-8 items-center gap-2 rounded-full border border-dashed px-3 text-xs font-medium transition-colors focus-visible:ring-2 focus-visible:ring-ring",
                isDefault
                  ? "border-border text-muted-foreground hover:bg-muted"
                  : "border-warning/40 bg-warning/10 text-warning hover:bg-warning/15",
              )}
            >
              <FlaskConical className="size-3.5" />
              <span className="hidden sm:inline">Mock data</span>
              <span className="hidden font-mono text-[11px] md:inline">
                {active.label.replace(" dataset", "")}
              </span>
              <ChevronDown className="size-3 opacity-70" />
            </button>
          </DropdownMenuTrigger>
        </TooltipTrigger>
        <TooltipContent side="bottom">
          Responses are served by MSW at the request layer.
        </TooltipContent>
      </Tooltip>
      <DropdownMenuContent align="end" className="w-[268px]">
        <DropdownMenuLabel className="text-2xs uppercase tracking-widest text-muted-foreground">
          Mock data
        </DropdownMenuLabel>
        <DropdownMenuRadioGroup
          value={scenario}
          onValueChange={(value) => onScenarioChange(value as MockScenario)}
        >
          {SCENARIOS.map((item) => (
            <DropdownMenuRadioItem
              key={item.value}
              value={item.value}
              className="items-start gap-2 py-2"
            >
              <span className="flex flex-col gap-0.5">
                <span className="text-[13px] font-medium">{item.label}</span>
                <span className="text-2xs text-muted-foreground">
                  {item.description}
                </span>
              </span>
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
        <DropdownMenuSeparator />
        <p className="px-2 py-1.5 font-mono text-[10px] leading-relaxed text-muted-foreground">
          Served by MSW. Disable with VITE_MOCK=0.
        </p>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
