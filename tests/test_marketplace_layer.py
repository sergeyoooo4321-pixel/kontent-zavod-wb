import zipfile

from app.category_resolver import _best_candidate, _flatten_ozon_tree, _flatten_wb_subjects
from app.config import Settings
from app.excel_export import build_zip
from app.models import CategoryMatch, GeneratedImage, MarketplaceProfile, ProductInput, ProductResult
from app.template_cache import parse_template_hint


def test_ozon_tree_flatten_inherits_category_id():
    tree = [
        {
            "category_name": "Household",
            "description_category_id": 100,
            "children": [{"type_name": "Washing powder", "type_id": 200}],
        }
    ]
    leaves = _flatten_ozon_tree(tree)
    assert len(leaves) == 1
    assert leaves[0].id == 100
    assert leaves[0].type_id == 200


def test_best_candidate_uses_product_words():
    candidates = _flatten_wb_subjects(
        [
            {"subjectID": 1, "parentName": "Beauty", "subjectName": "Perfume"},
            {"subjectID": 2, "parentName": "Household", "subjectName": "Washing powder"},
        ]
    )
    product = ProductInput(photo_index=1, sku="SKU1", name="Tide washing powder 400 g", brand="Tide")
    best = _best_candidate(product, candidates)
    assert best is not None
    assert best.id == 2


def test_best_candidate_prefers_substantive_product_word_over_generic_child_word():
    candidates = _flatten_wb_subjects(
        [
            {"subjectID": 1, "parentName": "Детское питание", "subjectName": "Молоко детское"},
            {"subjectID": 2, "parentName": "Красота и уход", "subjectName": "Мыло косметическое"},
            {"subjectID": 3, "parentName": "Хозяйственные товары", "subjectName": "Мыло металлическое"},
        ]
    )
    product = ProductInput(photo_index=1, sku="SOAP1", name="Детское твердое мыло 90 г", brand="Synergetic")
    best = _best_candidate(product, candidates)
    assert best is not None
    assert best.id == 2


def test_wb_search_candidates_are_preferred_over_full_tree(monkeypatch):
    from app.category_resolver import MarketplaceResolver

    captured = {}

    def fake_best(product, candidates):
        captured["ids"] = [candidate.id for candidate in candidates]
        return candidates[0]

    monkeypatch.setattr("app.category_resolver._best_candidate", fake_best)

    resolver = object.__new__(MarketplaceResolver)
    resolver._wb = object()
    product = ProductInput(photo_index=1, sku="SOAP1", name="Мыло 90 г")
    full_tree = [{"subjectID": 3, "parentName": "Хозяйственные товары", "subjectName": "Мыло металлическое"}]
    search_tree = [{"subjectID": 2, "parentName": "Красота", "subjectName": "Мыло косметическое"}]

    async def fake_search(_product):
        return _flatten_wb_subjects(search_tree)

    resolver._wb_search_leaves = fake_search

    import asyncio

    async def run():
        profile = type("Profile", (), {"wb_subject": None, "wb_fields": [], "missing_required": [], "warnings": []})()
        async def fake_subjects(_self):
            return full_tree

        async def fake_charcs(_self, _subject_id):
            return []

        resolver._wb = type("WB", (), {"subjects": fake_subjects, "subject_characteristics": fake_charcs})()

        async def fake_cached(_name, loader):
            return await loader()

        resolver._cached_json = fake_cached
        await MarketplaceResolver._resolve_wb(resolver, product, profile)

    asyncio.run(run())
    assert captured["ids"] == [2]


def test_parse_template_hint():
    assert parse_template_hint("template ozon 17034998 12345") == ("ozon", 17034998, 12345)
    assert parse_template_hint("template wb 98765") == ("wb", 98765, None)


def test_zip_contains_category_outputs(tmp_path):
    settings = Settings(TG_BOT_TOKEN="x", RUNTIME_DIR=tmp_path / "runtime", TEMPLATE_CACHE_DIR=tmp_path / "templates")
    result = ProductResult(
        input=ProductInput(photo_index=1, sku="A1", brand="Brand", name="Product"),
        images=[GeneratedImage(role="main", url="https://s3/main.jpg", key="main")],
        marketplace=MarketplaceProfile(
            ozon_category=CategoryMatch(marketplace="ozon", id=10, type_id=20, path="A / B", score=1),
            wb_subject=CategoryMatch(marketplace="wb", id=30, path="C / D", score=1),
            missing_required=["A1: Ozon Aroma is required"],
        ),
    )
    zip_path = tmp_path / "pack.zip"
    zip_path.write_bytes(build_zip([result], settings))
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        assert "category_report.xlsx" in names
        assert "marketplace_fields.json" in names
        assert "missing_templates.md" in names
        assert "ozon.xlsx" in names
        assert "wildberries.xlsx" in names
