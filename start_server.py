#!/usr/bin/env python
"""Simple script to start the Honest Congress API server."""
import uvicorn

if __name__ == "__main__":
    print("=" * 60)
    print("Starting Honest Congress API Server")
    print("=" * 60)
    print()
    print("Server will be available at: http://127.0.0.1:8000")
    print("API documentation at: http://127.0.0.1:8000/docs")
    print("Health check at: http://127.0.0.1:8000/health")
    print()
    print("Press Ctrl+C to stop the server")
    print("=" * 60)
    print()

    uvicorn.run(
        "src.api.main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
        log_level="info"
    )

