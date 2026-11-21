#!/usr/bin/env python3
"""
Backend startup script.
Runs FastAPI with uvicorn. Supports dynamic Render PORT binding,
production proxy headers, and debug reload control.
"""
import os
import uvicorn
from app.core.config import settings

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    is_prod = os.environ.get("RENDER") == "true" or os.environ.get("ENVIRONMENT") == "production"
    should_reload = settings.debug or not is_prod
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=port,
        reload=should_reload,
        log_level="info",
        proxy_headers=True,
        forwarded_allow_ips="*",
    )
