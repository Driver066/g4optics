"""Standard-library-only seed recipe shared by optical probes and acceptance."""
import hashlib

def seeds(key: str) -> tuple[int, int]:
    digest=hashlib.sha256(('steel-stack-v2-acceptance-20260929:'+key).encode()).digest()
    return tuple(1+int.from_bytes(digest[i:i+8],'big')%2147483398 for i in (0,8))
