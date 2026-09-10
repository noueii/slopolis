import React from "react"
import ReactDOM from "react-dom/client"

import "@fontsource-variable/hanken-grotesk"
import "@fontsource-variable/jetbrains-mono"
import "./index.css"

import App from "./App"

async function bootstrap(): Promise<void> {
  if (import.meta.env.VITE_MOCK === "worker") {
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
