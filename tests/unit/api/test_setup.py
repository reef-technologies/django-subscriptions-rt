def test_apiver_exports(apiver_module):
    assert sorted(name for name in dir(apiver_module) if not name.startswith("_")) == []
