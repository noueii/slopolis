/**
 * Error copy for the provider/admin surface (spec 10.2).
 *
 * Two refusals are states a workspace admin can act on rather than failures of
 * the request itself — a non-admin caller (403 `admin_required`) and a
 * deployment without `ENCRYPTION_KEY` (503 `vault_not_configured`) — so they
 * are explained in workspace terms instead of surfacing the raw envelope.
 */

import { ApiError } from "@/api/client"

const ADMIN_REQUIRED =
  "Only workspace admins can manage provider credentials and model assignments. Ask an admin of this workspace to make the change for you."

const VAULT_NOT_CONFIGURED =
  "This deployment has no workspace vault, so API keys cannot be stored or read. Set ENCRYPTION_KEY on the server (a base64, hex, or 32+ byte secret) and restart it, then reload this page."

export function adminErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof ApiError) {
    if (error.status === 403 && error.code === "admin_required") {
      return ADMIN_REQUIRED
    }
    if (error.status === 503 && error.code === "vault_not_configured") {
      return VAULT_NOT_CONFIGURED
    }
    return error.message
  }
  if (error instanceof Error && error.message !== "") return error.message
  return fallback
}
