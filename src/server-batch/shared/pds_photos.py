"""Photo ledger adapter preserving every role/version and existing preferred copies."""
import json
from pathlib import Path

from shared.photos_ledger import Copy, LedgerRecord


def merge_pds_photos(records, catalog, exif_dir=None):
    if not Path(catalog).exists():
        return
    for line in Path(catalog).read_text(encoding="utf-8").splitlines():
        product = json.loads(line)
        nid = product.get("nasa_id")
        if not nid:
            continue
        rec = records.setdefault(nid, LedgerRecord(nasa_id=nid))
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
    """Primary legacy selection plus verified PDS additions; no fallback blind spot."""
    from config import CREW_RAW_SOURCE_DIR
    from shared.pds import nasa_id
    out = {}
    for root in (mission.photos_raw_crew, CREW_RAW_SOURCE_DIR):
        if root.exists():
            for path in sorted(root.rglob("*")):
                if path.is_file() and path.suffix.lower() in {".nef", ".dng"}:
                    out.setdefault(nasa_id(path.name) or path.stem.lower(), path)
    catalog = mission.data_dir / "processed" / "pds" / "photos.jsonl"
    if catalog.exists():
        for line in catalog.read_text(encoding="utf-8").splitlines():
            product = json.loads(line)
            path = Path(product["path"])
            if product.get("nasa_id") and path.suffix.lower() in {".nef", ".dng"} and path.is_file():
                out.setdefault(product["nasa_id"], path)
    return out
