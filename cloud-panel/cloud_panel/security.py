from __future__ import annotations

import os
import re

_SAFE_ID_RE = re.compile(r'\A[A-Za-z0-9_-]{1,128}\Z')


def resolve_path_within(base_dir: str, relative_path: str, *, must_exist: bool = True) -> str:
    """Resolve a user-controlled path while containing symlinks inside base_dir."""
    if not isinstance(relative_path, str) or '\x00' in relative_path:
        raise ValueError('Chemin invalide')
    base = os.path.realpath(base_dir)
    candidate = os.path.realpath(os.path.join(base, relative_path))
    try:
        contained = os.path.commonpath((base, candidate)) == base
    except ValueError:
        contained = False
    if not contained or (must_exist and not os.path.exists(candidate)):
        raise ValueError('Chemin hors du repertoire autorise')
    return candidate


def validate_public_id(value: str, name: str = 'identifiant') -> str:
    if not isinstance(value, str) or not _SAFE_ID_RE.fullmatch(value):
        raise ValueError(f'{name} invalide')
    return value
