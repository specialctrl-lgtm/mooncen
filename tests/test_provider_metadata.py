from backend import provider_metadata


def test_choose_provider_label_uses_branch_for_collection_scope_title():
    assert provider_metadata.choose_provider_label(
        {
            "name": "\uC6A9\uC778\uC2DC\uB3C4\uC11C\uAD00 22\uAC1C\uAD00 \uC804\uCCB4 \uAD50\uC721\uAC15\uC88C",
            "branch": "\uC6A9\uC778\uC2DC\uB3C4\uC11C\uAD00",
        }
    ) == "\uC6A9\uC778\uC2DC\uB3C4\uC11C\uAD00"


def test_choose_provider_label_strips_collection_scope_without_branch():
    assert provider_metadata.choose_provider_label(
        {
            "name": "\uB300\uAD6C\uAD11\uC5ED\uC2DC \uD1B5\uD569\uC608\uC57D \uC804\uCCB4 \uAD50\uC721\uAC15\uC88C",
        }
    ) == "\uB300\uAD6C\uAD11\uC5ED\uC2DC \uD1B5\uD569\uC608\uC57D"


def test_choose_provider_label_uses_branch_for_operational_ledger_title():
    assert provider_metadata.choose_provider_label(
        {
            "name": "\uC778\uCC9C\uAD11\uC5ED\uC2DC \uC5F0\uC218\uAD6C \uC8FC\uBBFC\uC790\uCE58 \uAD50\uC721 \uC6D0\uC7A5",
            "branch": "\uC778\uCC9C\uAD11\uC5ED\uC2DC \uC5F0\uC218\uAD6C",
        }
    ) == "\uC778\uCC9C\uAD11\uC5ED\uC2DC \uC5F0\uC218\uAD6C"


def test_muni_provider_label_prefers_config_label(monkeypatch):
    label = "\uC11C\uC6B8\uB7F04050 \uC2E0\uCCAD\uD615 \uAC15\uC88C"
    fallback = "\uB3D9\uAD6D\uB300\uD559\uAD50 \uBB38\uD654\uAD00 147\uD638"

    monkeypatch.setattr(provider_metadata, "provider_defaults", lambda _provider: {"label": label})

    assert provider_metadata.provider_label("MUNI_SLL_SEOUL_GO_KR_A0D6D8A2", fallback) == label


def test_muni_provider_label_uses_fallback_without_config_label(monkeypatch):
    fallback = "\uB3D9\uAD6D\uB300\uD559\uAD50 \uBB38\uD654\uAD00 147\uD638"

    monkeypatch.setattr(provider_metadata, "provider_defaults", lambda _provider: {"label": "Muni Unknown Test"})

    assert provider_metadata.provider_label("MUNI_UNKNOWN_TEST", fallback) == fallback
