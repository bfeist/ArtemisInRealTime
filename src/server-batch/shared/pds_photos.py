"""Photo ledger adapter preserving every role/version and existing preferred copies."""
import json
import re
from pathlib import Path

from shared.photos_ledger import Copy, LedgerRecord


def photo_id(product):
    if product.get("bundle") == "artemis2_orion_camera":
        # Browse `_src` and scientific `_raw` representations share an observation.
        return re.sub(r"_(?:raw|src|proc)(?=_v\d+$)", "", Path(product["path"]).stem.lower())
    return product.get("nasa_id")


def merge_pds_photos(records, catalog, exif_dir=None):
    if not Path(catalog).exists():
        return
    # Orion products reuse numeric IDs from crew imagery. Detach any older
    # ID-only joins before adding their distinct camera/version records.
    for rec in records.values():
        rec.pds_products = [p for p in rec.pds_products
                            if p.get("bundle") != "artemis2_orion_camera"
                            or rec.nasa_id == photo_id(p)]
        if not rec.pds_products and "pds" in rec.exported_in:
            rec.exported_in.remove("pds")
            rec.exported = bool(rec.exported_in)
    for line in Path(catalog).read_text(encoding="utf-8").splitlines():
        product = json.loads(line)
        nid = photo_id(product)
        if not nid:
            continue
        rec = records.setdefault(nid, LedgerRecord(nasa_id=nid))
        if not rec.title and product.get("bundle") == "artemis2_orion_camera":
            rec.title = Path(product["path"]).stem
        rec.mark_exported_in("pds")
        # Catalog paths are distinct scientific products; never discard a TIFF or channel.
        rec.pds_products = [p for p in rec.pds_products if p["path"] != product["path"]]
        rec.pds_products.append(product)
        ext = Path(product["path"]).suffix.lower()
        if ext in {".nef", ".dng"} and not rec.copies.get("raw_crew"):
            exif_path = Path(exif_dir) / "raw_crew" / (nid + ".json") if exif_dir else None
            exif = json.loads(exif_path.read_text(encoding="utf-8")) if exif_path and exif_path.exists() else {}
            rec.copies["raw_crew"] = Copy(path=product["path"], exif=exif)
        if product.get("start_utc"):
            if not rec.utc:
                rec.utc, rec.utc_source = product["start_utc"], "pds_label"
            elif rec.utc != product["start_utc"]:
                rec.pds_time_conflicts = sorted(set(rec.pds_time_conflicts + [product["start_utc"]]))
    for rec in records.values():
        rec.pds_products.sort(key=lambda p: p["path"])


def crew_sources(mission):
    """Mission processing and bulk originals, plus verified PDS additions."""
    from shared.pds import nasa_id
    out = {}
    for root in (mission.photos_raw_crew, mission.photos_bulk_crew,
                 mission.data_dir / "raw/photos/nef_all", mission.bulk_dir / "raw/photos/nef_all"):
        if root.exists():
            for path in sorted(root.rglob("*")):
                allowed = {".nef", ".dng", ".jpg", ".jpeg"} if root.name == "nef_all" else {".nef", ".dng"}
                if path.is_file() and path.suffix.lower() in allowed:
                    out.setdefault(nasa_id(path.name) or path.stem.lower(), path)
    catalog = mission.data_dir / "processed" / "pds" / "photos.jsonl"
    if catalog.exists():
        for line in catalog.read_text(encoding="utf-8").splitlines():
            product = json.loads(line)
            path = Path(product["path"])
            if product.get("nasa_id") and path.suffix.lower() in {".nef", ".dng"} and path.is_file():
                out.setdefault(product["nasa_id"], path)
    return out
