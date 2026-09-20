import { describe, expect, it } from "vitest"

import { NAV_PATHS, parsePath, pathFor } from "./route"

describe("parsePath", () => {
  it("gives every sidebar destination its own path", () => {
    for (const [id, path] of Object.entries(NAV_PATHS)) {
      expect(parsePath(path)).toEqual({ kind: "nav", id })
    }
  })

  it("reads the redirect targets that carry a slash", () => {
    expect(parsePath("/sessions/sess_1")).toEqual({
      kind: "session",
      sessionId: "sess_1",
    })
    expect(parsePath("/repositories/acme/api-gateway")).toEqual({
      kind: "repository",
      fullName: "acme/api-gateway",
    })
  })

  it("tolerates a trailing slash and reads an encoded id", () => {
    expect(parsePath("/sessions/sess_1/")).toEqual({
      kind: "session",
      sessionId: "sess_1",
    })
    expect(parsePath("/sessions/a%2Fb")).toEqual({
      kind: "session",
      sessionId: "a/b",
    })
  })

  it("owns nothing else", () => {
    expect(parsePath("/sessions/sess_1/events")).toBeNull()
    expect(parsePath("/nope")).toBeNull()
  })

  it("round-trips through pathFor", () => {
    const routes = [
      ...Object.values(NAV_PATHS).map((path) => parsePath(path)),
      parsePath("/sessions/a%2Fb"),
      parsePath("/repositories/acme/api-gateway"),
    ]
    for (const route of routes) {
      expect(route).not.toBeNull()
      if (route) expect(parsePath(pathFor(route))).toEqual(route)
    }
  })
})
