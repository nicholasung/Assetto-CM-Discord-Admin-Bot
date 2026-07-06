"""HTTP file server for AC content downloads."""

from __future__ import annotations

import logging
from aiohttp import web
from pathlib import Path

log = logging.getLogger(__name__)


class ContentServer:
    """Simple HTTP file server for AC content."""
    
    def __init__(self, ac_root: Path, port: int):
        self.app = web.Application()
        self.ac_root = ac_root
        self.port = port
        
        # Route: /downloads/cars/ferrari/... → serve file
        self.app.router.add_get('/downloads/{path:.+}', self._serve)
        
        self.runner = None
        self.site = None
    
    async def _serve(self, request: web.Request) -> web.FileResponse:
        """Serve a file from content directory."""
        path = request.match_info['path']
        
        # Resolve the full path
        fpath = (self.ac_root / "content" / path).resolve()
        
        # Security: don't allow path traversal
        if not str(fpath).startswith(str(self.ac_root)):
            raise web.HTTPForbidden(text="Invalid path")
        
        if not fpath.exists():
            raise web.HTTPNotFound(text=f"{path} not found")
        
        if fpath.is_dir():
            raise web.HTTPBadRequest(text="Directories not supported")
        
        return web.FileResponse(fpath)
    
    async def start(self) -> None:
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, "0.0.0.0", self.port)
        await self.site.start()
        log.info("content server listening on :%d", self.port)
    
    async def stop(self) -> None:
        if self.runner:
            await self.runner.cleanup()
