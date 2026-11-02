"""AsyncAPI 2.6 conformance for the SSE streams. See docs/api.md
"Contract testing".

The SSE streams themselves are fully implemented (`adapters/http/routers/streams.py`)
— this is skipped because nobody has written an AsyncAPI spec or the
committed-spec-diffing machinery for it yet, not because there's nothing to
document.
"""

import pytest


@pytest.mark.contract
@pytest.mark.skip(reason="no AsyncAPI spec or CI diff step exists yet — see docs/api.md")
def test_generated_asyncapi_matches_committed_spec():
    pass
