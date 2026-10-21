"""Optional genuine GithubAnalyzer configuration components behind the bounded worker."""
from __future__ import annotations

import os
from pathlib import Path

from graphrag_core.repository_learning.adapter import PROTOCOL_VERSION, RepoAnalyzerAdapter, _failure

MAX_TEXT_BYTES = 16 * 1024
PARSERS = frozenset({'requirements', 'gitignore'})
COMPONENT_FILES = (
    'src/GithubAnalyzer/services/parsers/core/custom_parsers.py',
    'src/GithubAnalyzer/models/core/parsers.py',
    'src/GithubAnalyzer/models/core/base_model.py',
    'src/GithubAnalyzer/services/core/base_service.py',
    'src/GithubAnalyzer/utils/logging/__init__.py',
)


class GithubConfigurationAdapter(RepoAnalyzerAdapter):
    """Parse caller-supplied text without package-wide startup, acquisition or databases."""

    _backend_name = 'GithubAnalyzer'
    _backend_markers = COMPONENT_FILES

    @classmethod
    def from_environment(cls) -> 'GithubConfigurationAdapter':
        return cls(root=os.getenv('LM_PROXY_GITHUB_ANALYZER_ROOT') or None,
                   python=os.getenv('LM_PROXY_REPOANALYZER_PYTHON') or None)

    def _worker_path(self) -> Path:
        return Path(__file__).with_name('github_worker.py').absolute()

    async def analyze_configuration(self, project_id: str, source_id: str, text: str, parser: str) -> dict:
        """Return actual typed parser observations or a sanitized optional-backend failure."""
        if (not isinstance(project_id, str) or not 1 <= len(project_id) <= 128
                or ':' in project_id or '\0' in project_id
                or not isinstance(source_id, str) or not 1 <= len(source_id) <= 256
                or '\0' in source_id or not isinstance(parser, str) or parser not in PARSERS
                or not isinstance(text, str) or not text or '\0' in text):
            return _failure('invalid_request')
        try:
            if len(text.encode('utf-8')) > MAX_TEXT_BYTES:
                return _failure('input_too_large')
        except UnicodeError:
            return _failure('invalid_request')
        request = {'protocol_version': PROTOCOL_VERSION, 'operation': 'configuration',
                   'payload': {'project_id': project_id, 'source_id': source_id, 'text': text, 'parser': parser}}
        return await self._execute_request(request)
