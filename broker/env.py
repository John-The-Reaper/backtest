from __future__ import annotations

import os
from typing import Optional

from .exceptions import AuthError

_dotenv_loaded = False


def _ensure_dotenv() -> None:
    """Charge .env une seule fois si python-dotenv est disponible."""
    global _dotenv_loaded
    if _dotenv_loaded:
        return
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    _dotenv_loaded = True


def getenv(key: str, default: Optional[str] = None, required: bool = False) -> Optional[str]:
    """
    Lit une variable d'env. Charge .env (si python-dotenv installe) au premier appel.

    Si `required=True` et que la variable est absente ou vide, leve AuthError.
    """
    _ensure_dotenv()
    val = os.environ.get(key)
    if val is None or val == "":
        if required:
            raise AuthError(
                f"Variable d'env requise manquante : {key}. "
                f"Definir dans .env ou dans l'environnement."
            )
        return default
    return val
