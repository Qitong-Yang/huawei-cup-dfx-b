"""Unit tests for dfx_repair.retrieve.source_map."""

from pathlib import Path

from dfx_repair.retrieve import MapStatus, SourceIndex


def _touch(root: Path, rel: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("// x\n", encoding="utf-8")


def test_resolve_unique_multiple_and_missing(tmp_path: Path):
    _touch(tmp_path, "features/a/src/main/ets/pages/Index.ets")
    _touch(tmp_path, "features/b/src/main/ets/pages/Index.ets")
    _touch(tmp_path, "components/x/src/main/ets/Util.ets")
    _touch(tmp_path, "oh_modules/dep/Util.ets")  # dependency dirs are not indexed
    index = SourceIndex(tmp_path)

    full = index.resolve("features/a/src/main/ets/pages/Index.ets")
    assert full.status is MapStatus.UNIQUE
    assert full.candidates == ("features/a/src/main/ets/pages/Index.ets",)

    bare = index.resolve("Index.ets")
    assert bare.status is MapStatus.MULTIPLE and len(bare.candidates) == 2

    assert index.resolve("Util.ets").status is MapStatus.UNIQUE
    assert index.resolve("Missing.ets").status is MapStatus.NOT_FOUND


def test_device_path_resolves_by_suffix(tmp_path: Path):
    _touch(tmp_path, "features/home/src/main/ets/viewModels/ExpressPageVM.ets")
    index = SourceIndex(tmp_path)
    mapping = index.resolve("/data/storage/el1/bundle/entry/ets/viewModels/ExpressPageVM.ets")
    assert mapping.status is MapStatus.UNIQUE
    assert mapping.matched_parts == 3  # ets/viewModels/ExpressPageVM.ets
