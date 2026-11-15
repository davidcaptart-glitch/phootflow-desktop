"""Conexiones HTTPS que funcionan también en PCs donde Python no valida certificados con el almacén de Windows."""
from __future__ import annotations

import ssl
import urllib.request

_CTX = None


def context() -> ssl.SSLContext:
    """Contexto TLS con las autoridades de `certifi` (si está) más las del sistema."""
    global _CTX
    if _CTX is None:
        ctx = ssl.create_default_context()
        try:
            import certifi
            ctx.load_verify_locations(cafile=certifi.where())
        except Exception:
            pass
        _CTX = ctx
    return _CTX


def urlopen(req, timeout: float = 30):
    return urllib.request.urlopen(req, timeout=timeout, context=context())
