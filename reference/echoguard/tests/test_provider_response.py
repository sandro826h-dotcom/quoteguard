from echoguard.providers import ProviderResponse


def test_provider_response_metadata_default():
    r = ProviderResponse(text="{}", model_name="x")
    assert r.metadata == {}
