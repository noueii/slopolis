import { createServer } from "@mswjs/http-middleware"

import { handlers } from "./handlers"

const port = Number(import.meta.env.MOCK_PORT ?? 5174)

createServer(...handlers).listen(port, () => {
  console.log(`mock api listening on http://localhost:${port}`)
})
