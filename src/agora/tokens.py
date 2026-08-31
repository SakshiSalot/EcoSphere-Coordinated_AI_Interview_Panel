"""RTC token minting.

Agora channels require token auth, and both the candidate and every agent need
one. This must stay server-side: putting the App Certificate in browser
JavaScript hands anyone your account.
"""

import time

from src import config

_PUBLISHER = 1

try:  # the common PyPI package
    from agora_token_builder import RtcTokenBuilder as _Builder
except ImportError:  # pragma: no cover - the official repo vendors it here
    try:
        from src.agora.RtcTokenBuilder2 import RtcTokenBuilder as _Builder
    except ImportError:
        _Builder = None


def build_token(channel: str, uid: int, ttl_seconds: int = 3600) -> str:
    """Mint a publisher token for `uid` on `channel`.

    Returns "" when the project has no App Certificate enabled — Agora accepts
    an empty token in App-ID-only mode, so a missing certificate is a valid
    configuration rather than an error.
    """
    if not config.AGORA_APP_CERTIFICATE:
        return ""
    if _Builder is None:
        raise RuntimeError(
            "No RTC token builder available. `pip install agora-token-builder`."
        )
    expires_at = int(time.time()) + ttl_seconds
    return _Builder.buildTokenWithUid(
        config.AGORA_APP_ID,
        config.AGORA_APP_CERTIFICATE,
        channel,
        uid,
        _PUBLISHER,
        expires_at,
    )
