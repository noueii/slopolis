import { Skeleton } from "@/components/ui/skeleton"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"

const ROWS = 10

export function SessionsSkeleton() {
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-card">
      <Table>
        <TableHeader>
          <TableRow className="hover:bg-transparent">
            {[
              "Session",
              "Targets",
              "Status",
              "Model",
              "Tokens",
              "Cost",
              "Triggered by",
              "Created",
            ].map((label) => (
              <TableHead
                key={label}
                className="h-10 text-2xs uppercase tracking-wider"
              >
                {label}
              </TableHead>
            ))}
            <TableHead className="w-8" />
          </TableRow>
        </TableHeader>
        <TableBody>
          {Array.from({ length: ROWS }).map((_, index) => (
            <TableRow key={index} className="hover:bg-transparent">
              <TableCell className="py-3 pl-4">
                <Skeleton className="h-3.5 w-[220px]" />
                <Skeleton className="mt-1.5 h-2.5 w-[120px]" />
              </TableCell>
              <TableCell>
                <Skeleton className="h-5 w-[150px] rounded" />
              </TableCell>
              <TableCell>
                <Skeleton className="h-5 w-[78px] rounded-full" />
              </TableCell>
              <TableCell>
                <Skeleton className="h-3.5 w-[104px]" />
                <Skeleton className="mt-1.5 h-2.5 w-[64px]" />
              </TableCell>
              <TableCell>
                <Skeleton className="ml-auto h-3.5 w-[48px]" />
              </TableCell>
              <TableCell>
                <Skeleton className="ml-auto h-3.5 w-[52px]" />
              </TableCell>
              <TableCell>
                <div className="flex items-center gap-2">
                  <Skeleton className="size-5 rounded-full" />
                  <Skeleton className="h-3 w-[72px]" />
                </div>
              </TableCell>
              <TableCell>
                <Skeleton className="h-3.5 w-[60px]" />
                <Skeleton className="mt-1.5 h-2.5 w-[92px]" />
              </TableCell>
              <TableCell />
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}
