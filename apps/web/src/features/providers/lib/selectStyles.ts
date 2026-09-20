/**
 * Styling for the admin surface's native selects.
 *
 * They are plain `<select>`s rather than the Radix `Select` used elsewhere in
 * the app: the rows they sit in list at most a workspace's models or
 * credentials, and Radix's `Select` needs pointer-capture APIs that the jsdom
 * environment does not implement, which would leave these controls untestable.
 */
export const selectClasses =
  "h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
