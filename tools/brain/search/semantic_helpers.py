"""Compatibility alias for retrieval policy now owned by memory."""

import sys

from memory import retrieval_policy as _retrieval_policy


sys.modules[__name__] = _retrieval_policy
