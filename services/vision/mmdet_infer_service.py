from __future__ import annotations

import base64
import io
import os
from functools import lru_cache

import cv2
import numpy as np
from fastapi import FastAPI, HTTPException
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field

try:
    from mmdet.apis import inference_detector, init_detector
except Exception:  # pragma: no cover - optional dependency
    inference_detector = None
    init_detector = None


class DetectRequest(BaseModel):
    file_name: str
    image_data_url: str
    confidence: float = 0.25
    iou: float = 0.7
    image_size: int = 960


class Detection(BaseModel):
    label: str
    class_id: int
    confidence: float
    bbox: list[int]
    polygon: list[int] | None = None


class DetectResponse(BaseModel):
    file_name: str
    detector_name: str
    image: dict[str, int]
    detections: list[Detection] = Field(default_factory=list)


app = FastAPI(title="Chassis MMDetection Segmentation Service", version="0.1.0")


@lru_cache(maxsize=1)
def _model():
    if init_detector is None:
        raise RuntimeError("mmdetection is not installed")
    config_file = os.getenv("MMDET_CONFIG_FILE")
    checkpoint_file = os.getenv("MMDET_CHECKPOINT_FILE")
    if not config_file or not os.path.exists(config_file):
        raise FileNotFoundError(f"MMDET_CONFIG_FILE does not exist: {config_file}")
    if not checkpoint_file or not os.path.exists(checkpoint_file):
        raise FileNotFoundError(f"MMDET_CHECKPOINT_FILE does not exist: {checkpoint_file}")
    device = os.getenv("MMDET_DEVICE", "cuda:0")
    return init_detector(config_file, checkpoint_file, device=device)


@app.get("/health")
def health() -> dict:
    config_file = os.getenv("MMDET_CONFIG_FILE")
    checkpoint_file = os.getenv("MMDET_CHECKPOINT_FILE")
    return {
        "ok": bool(init_detector is not None and config_file and checkpoint_file and os.path.exists(config_file) and os.path.exists(checkpoint_file)),
        "config_file": config_file,
        "checkpoint_file": checkpoint_file,
        "device": os.getenv("MMDET_DEVICE", "cuda:0"),
        "dependency_ready": init_detector is not None,
    }


@app.post("/detect", response_model=DetectResponse)
def detect(payload: DetectRequest) -> DetectResponse:
    image = _decode_image(payload.image_data_url)
    try:
        model = _model()
    except (RuntimeError, FileNotFoundError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    result = inference_detector(model, image)
    pred_instances = getattr(result, "pred_instances", None)
    if pred_instances is None:
        return DetectResponse(file_name=payload.file_name, detector_name="mmdet_instance_seg", image={"width": image.width, "height": image.height})

    class_names = _class_names(model)
    detections: list[Detection] = []
    scores = _to_numpy(getattr(pred_instances, "scores", None))
    labels = _to_numpy(getattr(pred_instances, "labels", None), dtype=np.int64)
    bboxes = _to_numpy(getattr(pred_instances, "bboxes", None))
    masks = getattr(pred_instances, "masks", None)

    if scores is None or labels is None or bboxes is None:
        return DetectResponse(file_name=payload.file_name, detector_name="mmdet_instance_seg", image={"width": image.width, "height": image.height})

    for index in range(len(scores)):
        score = float(scores[index])
        if score < payload.confidence:
            continue
        class_id = int(labels[index])
        bbox = [int(value) for value in bboxes[index].tolist()]
        polygon = None
        if masks is not None:
            polygon = _mask_to_polygon(masks[index], image.width, image.height)
        detections.append(
            Detection(
                label=str(class_names[class_id]) if class_id < len(class_names) else str(class_id),
                class_id=class_id,
                confidence=round(score, 4),
                bbox=bbox,
                polygon=polygon,
            )
        )

    return DetectResponse(
        file_name=payload.file_name,
        detector_name="mmdet_instance_seg",
        image={"width": image.width, "height": image.height},
        detections=detections,
    )


def _class_names(model) -> list[str]:
    dataset_meta = getattr(model, "dataset_meta", None) or {}
    classes = dataset_meta.get("classes") or getattr(model, "CLASSES", None) or []
    return [str(item) for item in classes]


def _decode_image(data_url: str) -> Image.Image:
    if "," not in data_url:
        raise HTTPException(status_code=400, detail="invalid image data url")
    _, encoded = data_url.split(",", 1)
    try:
        raw = base64.b64decode(encoded)
        image = Image.open(io.BytesIO(raw))
        image.load()
    except (OSError, ValueError, UnidentifiedImageError) as exc:
        raise HTTPException(status_code=400, detail="unsupported or invalid image") from exc
    return image.convert("RGB")


def _to_numpy(value, dtype=None):
    if value is None:
        return None
    if hasattr(value, "detach"):
        value = value.detach()
    if hasattr(value, "cpu"):
        value = value.cpu()
    if hasattr(value, "numpy"):
        value = value.numpy()
    return np.asarray(value, dtype=dtype)


def _mask_to_polygon(mask, width: int, height: int) -> list[int] | None:
    mask_array = _to_numpy(mask, dtype=np.uint8)
    if mask_array is None:
        return None
    if mask_array.ndim == 3:
        mask_array = mask_array.squeeze()
    mask_array = (mask_array > 0).astype("uint8") * 255
    if mask_array.shape[:2] != (height, width):
        mask_array = cv2.resize(mask_array, (width, height), interpolation=cv2.INTER_NEAREST)
    contours, _ = cv2.findContours(mask_array, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    contour = max(contours, key=cv2.contourArea)
    if cv2.contourArea(contour) < 20:
        return None
    epsilon = 0.01 * cv2.arcLength(contour, True)
    approx = cv2.approxPolyDP(contour, epsilon, True)
    if len(approx) < 3:
        x, y, w, h = cv2.boundingRect(contour)
        return [x, y, x + w, y, x + w, y + h, x, y + h]
    polygon: list[int] = []
    for point in approx.reshape(-1, 2):
        polygon.extend([int(point[0]), int(point[1])])
    return polygon
