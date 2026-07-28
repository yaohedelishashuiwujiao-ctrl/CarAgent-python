from __future__ import annotations

import base64
import io
import json
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime
from math import fabs
from functools import lru_cache
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from backend.app.repositories.metadata import MetadataRepository
from backend.app.repositories.dataset import DatasetRepository
from backend.app.schemas_dataset import DatasetAnnotation, DatasetImage


@dataclass(frozen=True)
class YoloSegExportArtifact:
    path: str
    export_name: str
    manifest: dict


@dataclass(frozen=True)
class CocoSegExportArtifact:
    path: str
    export_name: str
    manifest: dict


def build_yolo_seg_export(dataset_repository: DatasetRepository, metadata_repository: MetadataRepository) -> YoloSegExportArtifact:
    export_name = "chassis_parts_yolo_v0"
    classes = [item for item in metadata_repository.list_entity_types("component")]
    class_map = {item.id: index for index, item in enumerate(sorted(classes, key=lambda item: item.id))}
    class_names = [item.code for item in sorted(classes, key=lambda item: item.id)]

    images = dataset_repository.list_images()
    annotations = dataset_repository.list_annotations()
    annotations_by_image: dict[int, list[DatasetAnnotation]] = {}
    for annotation in annotations:
        annotations_by_image.setdefault(annotation.image_id, []).append(annotation)

    temp_file = tempfile.NamedTemporaryFile(prefix=f"{export_name}_", suffix=".zip", delete=False)
    temp_path = temp_file.name
    temp_file.close()

    manifest = {
        "export_name": export_name,
        "format": "yolo-seg",
        "generated_at": datetime.now().replace(microsecond=0).isoformat(),
        "class_count": len(class_names),
        "classes": [
            {"class_id": index, "entity_type_id": item.id, "code": item.code, "name": item.name}
            for index, item in enumerate(sorted(classes, key=lambda item: item.id))
        ],
        "images": [],
        "warnings": [],
    }

    with zipfile.ZipFile(temp_path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "data.yaml",
            _build_data_yaml(class_names),
        )
        archive.writestr(
            "classes.txt",
            "\n".join(class_names) + ("\n" if class_names else ""),
        )

        for image in images:
            image_annotations = annotations_by_image.get(image.id, [])
            if not image_annotations:
                continue

            split = image.split if image.split in {"train", "val", "test"} else "train"
            if image.split not in {"train", "val", "test"}:
                manifest["warnings"].append(
                    {
                        "image_id": image.id,
                        "file_name": image.file_name,
                        "warning": f"split={image.split} is not train/val/test; exported as train",
                    }
                )

            decoded = _resolve_image(image.file_name, image.image_data_url)
            if decoded is None:
                manifest["warnings"].append(
                    {
                        "image_id": image.id,
                        "file_name": image.file_name,
                        "warning": "missing or invalid image_data_url; skipped",
                    }
                )
                continue

            image_stem = f"{image.id:06d}"
            image_path = f"images/{split}/{image_stem}.jpg"
            label_path = f"labels/{split}/{image_stem}.txt"
            archive.writestr(image_path, _encode_jpeg(decoded))

            label_lines: list[str] = []
            for annotation in image_annotations:
                class_id = class_map.get(annotation.entity_type_id)
                if class_id is None:
                    manifest["warnings"].append(
                        {
                            "image_id": image.id,
                            "annotation_id": annotation.id,
                            "warning": f"entity_type_id={annotation.entity_type_id} not found in export class map; skipped",
                        }
                    )
                    continue
                polygon = _annotation_to_polygon(annotation, decoded.width, decoded.height)
                if polygon is None:
                    manifest["warnings"].append(
                        {
                            "image_id": image.id,
                            "annotation_id": annotation.id,
                            "warning": "invalid annotation geometry; skipped",
                        }
                    )
                    continue
                label_lines.append(
                    f"{class_id} " + " ".join(f"{value:.6f}" for value in polygon)
                )

            if not label_lines:
                manifest["warnings"].append(
                    {
                        "image_id": image.id,
                        "file_name": image.file_name,
                        "warning": "no valid annotations were exported for this image",
                    }
                )
                continue

            archive.writestr(label_path, "\n".join(label_lines) + "\n")
            manifest["images"].append(
                {
                    "image_id": image.id,
                    "file_name": image.file_name,
                    "split": split,
                    "annotation_count": len(label_lines),
                    "width": decoded.width,
                    "height": decoded.height,
                    "image_path": image_path,
                    "label_path": label_path,
                }
            )

        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))

    return YoloSegExportArtifact(path=temp_path, export_name=export_name, manifest=manifest)


def build_coco_seg_export(dataset_repository: DatasetRepository, metadata_repository: MetadataRepository) -> CocoSegExportArtifact:
    export_name = "chassis_parts_coco_seg_v0"
    classes = sorted(metadata_repository.list_entity_types("component"), key=lambda item: item.id)
    category_map = {item.id: index + 1 for index, item in enumerate(classes)}
    systems = {item.id: item for item in metadata_repository.list_systems()}

    images = dataset_repository.list_images()
    annotations = dataset_repository.list_annotations()
    annotations_by_image: dict[int, list[DatasetAnnotation]] = {}
    for annotation in annotations:
        annotations_by_image.setdefault(annotation.image_id, []).append(annotation)

    temp_file = tempfile.NamedTemporaryFile(prefix=f"{export_name}_", suffix=".zip", delete=False)
    temp_path = temp_file.name
    temp_file.close()

    manifest = {
        "export_name": export_name,
        "format": "coco-instance-seg",
        "generated_at": datetime.now().replace(microsecond=0).isoformat(),
        "category_count": len(classes),
        "categories": [
            {"category_id": category_map[item.id], "entity_type_id": item.id, "code": item.code, "name": item.name}
            for item in classes
        ],
        "splits": {"train": [], "val": [], "test": []},
        "warnings": [],
    }

    coco_by_split = {split: _empty_coco_payload() for split in ("train", "val", "test")}

    with zipfile.ZipFile(temp_path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for image in images:
            split = image.split if image.split in {"train", "val", "test"} else "train"
            if image.split not in {"train", "val", "test"}:
                manifest["warnings"].append(
                    {
                        "image_id": image.id,
                        "file_name": image.file_name,
                        "warning": f"split={image.split} is not train/val/test; exported as train",
                    }
                )
            decoded = _resolve_image(image.file_name, image.image_data_url)
            if decoded is None:
                manifest["warnings"].append(
                    {
                        "image_id": image.id,
                        "file_name": image.file_name,
                        "warning": "missing or invalid image_data_url; skipped",
                    }
                )
                continue

            image_path = f"images/{split}/{image.id:06d}.jpg"
            archive.writestr(image_path, _encode_jpeg(decoded))
            coco_by_split[split]["images"].append(
                {
                    "id": image.id,
                    "file_name": f"{image.id:06d}.jpg",
                    "width": decoded.width,
                    "height": decoded.height,
                }
            )
            manifest["splits"][split].append(image.id)

            for annotation in annotations_by_image.get(image.id, []):
                category_id = category_map.get(annotation.entity_type_id)
                if category_id is None:
                    manifest["warnings"].append(
                        {
                            "image_id": image.id,
                            "annotation_id": annotation.id,
                            "warning": f"entity_type_id={annotation.entity_type_id} not found in category map; skipped",
                        }
                    )
                    continue
                polygon = _annotation_to_polygon(annotation, decoded.width, decoded.height)
                if polygon is None:
                    manifest["warnings"].append(
                        {
                            "image_id": image.id,
                            "annotation_id": annotation.id,
                            "warning": "invalid annotation geometry; skipped",
                        }
                    )
                    continue
                segmentation = [polygon]
                bbox = _polygon_to_bbox(polygon)
                coco_by_split[split]["annotations"].append(
                    {
                        "id": len(coco_by_split[split]["annotations"]) + 1,
                        "image_id": image.id,
                        "category_id": category_id,
                        "segmentation": segmentation,
                        "area": round(_polygon_area(polygon), 2),
                        "bbox": [round(value, 2) for value in bbox],
                        "iscrowd": 0,
                    }
                )

        for split, payload in coco_by_split.items():
            payload["categories"] = [
                {
                    "id": category_map[item.id],
                    "name": item.code,
                    "supercategory": systems[item.default_system_id].code if item.default_system_id in systems else "unknown",
                }
                for item in classes
            ]
            archive.writestr(f"annotations/{split}.json", json.dumps(payload, ensure_ascii=False, indent=2))

        archive.writestr("metainfo.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))

    return CocoSegExportArtifact(path=temp_path, export_name=export_name, manifest=manifest)


def _build_data_yaml(class_names: list[str]) -> str:
    lines = [
        "path: .",
        "train: images/train",
        "val: images/val",
        "test: images/test",
        f"nc: {len(class_names)}",
        "names:",
    ]
    for index, name in enumerate(class_names):
        lines.append(f"  {index}: {name}")
    return "\n".join(lines) + "\n"


def _decode_image(image_data_url: str | None) -> Image.Image | None:
    if not image_data_url or "," not in image_data_url:
        return None
    _, encoded = image_data_url.split(",", 1)
    try:
        raw = base64.b64decode(encoded)
        image = Image.open(io.BytesIO(raw))
        image.load()
    except (OSError, ValueError, UnidentifiedImageError):
        return None
    return image.convert("RGB")


def _resolve_image(file_name: str, image_data_url: str | None) -> Image.Image | None:
    image = _decode_image(image_data_url)
    if image is not None:
        return image
    return _load_local_image(file_name)


def _encode_jpeg(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=92)
    return buffer.getvalue()


@lru_cache(maxsize=128)
def _load_local_image(file_name: str) -> Image.Image | None:
    search_roots = [Path("resources"), Path("resources/chassis_seed_dataset"), Path("resources/chassis_seed_dataset_v1")]
    for root in search_roots:
        if not root.exists():
            continue
        for path in root.rglob(file_name):
            if path.is_file():
                try:
                    image = Image.open(path)
                    image.load()
                    return image.convert("RGB")
                except (OSError, UnidentifiedImageError):
                    continue
    return None


def _annotation_to_polygon(annotation: DatasetAnnotation, width: int, height: int) -> list[float] | None:
    geometry = [float(value) for value in annotation.bbox]
    if annotation.annotation_type == "bbox":
        if len(geometry) != 4:
            return None
        x1, y1, x2, y2 = geometry
        geometry = [x1, y1, x2, y1, x2, y2, x1, y2]
    elif annotation.annotation_type == "polygon":
        if len(geometry) < 6 or len(geometry) % 2 != 0:
            return None
    else:
        return None

    normalized: list[float] = []
    for index in range(0, len(geometry), 2):
        x = max(0.0, min(float(width), geometry[index]))
        y = max(0.0, min(float(height), geometry[index + 1]))
        normalized.append(x / float(width))
        normalized.append(y / float(height))
    return normalized


def _empty_coco_payload():
    return {
        "info": {
            "description": "Chassis parts instance segmentation dataset",
            "version": "v0",
            "year": datetime.now().year,
        },
        "licenses": [],
        "images": [],
        "annotations": [],
        "categories": [],
    }


def _polygon_area(points: list[float]) -> float:
    if len(points) < 6 or len(points) % 2 != 0:
        return 0.0
    coords = [(points[i], points[i + 1]) for i in range(0, len(points), 2)]
    total = 0.0
    for index, (x1, y1) in enumerate(coords):
        x2, y2 = coords[(index + 1) % len(coords)]
        total += x1 * y2 - x2 * y1
    return fabs(total) / 2.0


def _polygon_to_bbox(points: list[float]) -> list[float]:
    xs = points[0::2]
    ys = points[1::2]
    return [min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)]
