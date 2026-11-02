"""Schema conformance: every response validated against the generated
OpenAPI schema. Committed-spec diffing: `openapi.json` is committed, CI
regenerates and diffs it. See docs/api.md "Contract testing".

The API itself is fully implemented (`GET /openapi.json` is live, FastAPI's
own default) — these two are skipped because no one has written the
committed-spec-diffing machinery or the response-schema-validation harness
yet, not because there's nothing to test against.
"""

import pytest


@pytest.mark.contract
@pytest.mark.skip(reason="no committed openapi.json / CI diff step yet — see docs/api.md")
def test_generated_openapi_matches_committed_spec():
    pass


@pytest.mark.contract
@pytest.mark.skip(reason="no response-schema-validation harness yet — see docs/api.md")
def test_every_documented_response_validates_against_its_schema():
    pass
