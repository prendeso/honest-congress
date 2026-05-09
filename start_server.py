#!/usr/bin/env python
"""Start the Honest Congress API server.

Reads `PORT` from the environment so the same script works locally and on
PaaS hosts that inject the port (Railway, Heroku, Fly, etc.). Binds to
`0.0.0.0` so the platform's ingress can reach the container; reload mode
is only enabled when `ENV=dev`.
"""

import os

import uvicorn


def main() -> None:
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    env = os.getenv("ENV", "dev").lower()
    reload = env == "dev"

    print("=" * 60)
    print("Starting Honest Congress API Server")
    print("=" * 60)
    print(f"Listening on http://{host}:{port}  (env={env}, reload={reload})")
    print("Docs:        /docs")
    print("Health:      /health")
    print("=" * 60)

    uvicorn.run(
        "src.api.main:app",
        host=host,
        port=port,
        reload=reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()
