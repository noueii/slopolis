/**
 * Browser MSW worker. Started from `main.tsx` only when mock mode is enabled
 * (dev builds or `VITE_MOCK=1`). Never imported by feature UI code.
 */

import { setupWorker } from "msw/browser"

import { handlers } from "./handlers"

export const worker = setupWorker(...handlers)
