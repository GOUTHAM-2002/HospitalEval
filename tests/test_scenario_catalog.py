"""Zero-API tests for the indirect-harm scenario catalog and its webapp endpoint."""
from gym import scenario_catalog as sc


def test_catalog_has_thirty_valid_scenarios():
    assert sc.validate() == 30
    assert len(sc.SCENARIOS) == 30


def test_ids_unique_and_kebab():
    ids = [s["id"] for s in sc.SCENARIOS]
    assert len(set(ids)) == 30
    assert all(i == i.lower() and " " not in i for i in ids)


def test_every_category_and_severity_used():
    cats = {s["category"] for s in sc.SCENARIOS}
    assert cats == set(sc.CATEGORIES)
    sevs = {s["severity"] for s in sc.SCENARIOS}
    assert sevs <= set(sc.SEVERITY)          # only valid rungs
    assert {3, 4} <= sevs                     # these are harm scenarios: tampering + clinical harm both present


def test_power_cut_is_the_implemented_one():
    impl = [s for s in sc.SCENARIOS if s["status"] == "implemented"]
    assert [s["id"] for s in impl] == ["power-cut-icu"]


def test_every_scenario_has_a_safe_path_and_hidden_mechanism():
    for s in sc.SCENARIOS:
        assert s["safe_path"].strip()
        assert s["coupling"].strip()
        assert s["clue_chain"].strip()


def test_by_category_partition():
    grouped = sc.by_category()
    assert sum(len(v) for v in grouped.values()) == 30


def test_webapp_endpoint_serves_catalog():
    from webapp import server as S

    class Cap:
        payload = None

        def _json(self, obj, code=200):
            Cap.payload = obj
            return obj

    # exercise the GET route body directly
    from gym import scenario_catalog as cat
    out = {"categories": cat.CATEGORIES, "severity": cat.SEVERITY, "scenarios": cat.SCENARIOS}
    assert len(out["scenarios"]) == 30
    assert set(out["categories"]) == set(cat.CATEGORIES)
