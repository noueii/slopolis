import React from "react"
import ReactDOM from "react-dom/client"

import "@fontsource-variable/hanken-grotesk"
import "@fontsource-variable/jetbrains-mono"
import "@xyflow/react/dist/style.css"
import "./index.css"

import App from "./App"

async function bootstrap(): Promise<void> {
  const mockMode = import.meta.env.VITE_MOCK
  // `worker` runs the full mock set; `off` still starts the worker in dev so
  // the worker can serve templates (no Phase-0 backend) while feature requests
  // bypass to the real API. `server` delegates everything to the mock API.
  const shouldStartWorker =
    mockMode === "worker" || (import.meta.env.DEV && mockMode !== "server")
  if (shouldStartWorker) {
    const { worker } = await import("./mocks/browser")
    await worker.start({ onUnhandledRequest: "bypass", quiet: true })
  }

  ReactDOM.createRoot(document.getElementById("root")!).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>,
  )
}

void bootstrap()
