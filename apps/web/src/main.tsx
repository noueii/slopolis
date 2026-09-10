import React from "react"
import ReactDOM from "react-dom/client"

import "@fontsource-variable/hanken-grotesk"
import "@fontsource-variable/jetbrains-mono"
import "./index.css"

import App from "./App"
import { isMockModeEnabled } from "./api/client"

/**
 * Boots the app. Request mocking is started here, behind a single flag, so it
 * can be switched off (`VITE_MOCK=0`) without touching any UI code.
 */
async function bootstrap(): Promise<void> {
  if (isMockModeEnabled()) {
    const { worker } = await import("./mocks/browser")
    await worker.start({
      onUnhandledRequest: "bypass",
      quiet: true,
      serviceWorker: { url: "/mockServiceWorker.js" },
    })
  }

  ReactDOM.createRoot(document.getElementById("root")!).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>,
  )
}

void bootstrap()
