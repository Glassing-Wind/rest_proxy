"""Compatibility alias for retrieval fallbacks now owned by memory."""

import sys

from memory import retrieval_fallbacks as _retrieval_fallbacks


sys.modules[__name__] = _retrieval_fallbacks
