/** Minimises the New Review composer into a sticky bar once it scrolls away. */

import { useCallback, useEffect, useState, type RefObject } from "react"

export interface StickyCollapse {
  collapsed: boolean
  expand: () => void
}

const COLLAPSE_AT = 112
const EXPAND_AT = 8

export function useStickyCollapse(
  containerRef: RefObject<HTMLElement>,
  composerRef: RefObject<HTMLElement>,
): StickyCollapse {
  const [collapsed, setCollapsed] = useState(false)

  useEffect(() => {
    const container = containerRef.current
    const composer = composerRef.current
    const scroller = container?.closest("main")
    if (!container || !composer || !(scroller instanceof HTMLElement)) return

    let next = false

    const evaluate = () => {
      const scrollerTop = scroller.getBoundingClientRect().top
      const rect = composer.getBoundingClientRect()
      let shouldCollapse = next
      if (!next && rect.bottom < scrollerTop + COLLAPSE_AT) {
        shouldCollapse = true
      } else if (next && rect.top > scrollerTop + EXPAND_AT) {
        shouldCollapse = false
      }
      if (shouldCollapse !== next) {
        next = shouldCollapse
        setCollapsed(shouldCollapse)
      }
    }

    evaluate()
    scroller.addEventListener("scroll", evaluate, { passive: true })
    window.addEventListener("resize", evaluate)
    return () => {
      scroller.removeEventListener("scroll", evaluate)
      window.removeEventListener("resize", evaluate)
    }
  }, [containerRef, composerRef])

  const expand = useCallback(() => {
    const scroller = containerRef.current?.closest("main")
    if (scroller instanceof HTMLElement) {
      const reduced = window.matchMedia("(prefers-reduced-motion: reduce)")
        .matches
      scroller.scrollTo({ top: 0, behavior: reduced ? "auto" : "smooth" })
    }
    setCollapsed(false)
  }, [containerRef])

  return { collapsed, expand }
}
