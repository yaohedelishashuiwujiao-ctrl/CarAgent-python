from __future__ import annotations

import base64
import io
import os
from functools import lru_cache

from fastapi import FastAPI, HTTPException
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field
from ultralytics import YOLO


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


class DetectResponse(BaseModel):
    file_name: str
    detector_name: str
    image: dict[str, int]
    detections: list[Detection] = Field(default_factory=list)


app = FastAPI(title="Chassis YOLO Detector", version="0.1.0")


@lru_cache(maxsize=1)
def model() -> YOLO:
    model_path = os.getenv("CHASSIS_MODEL_PATH", "runs/chassis/yolo_chassis_parts/weights/best.pt")
    if not os.path.exists(model_path):
        raise FileNotFoundError(f"model file does not exist: {model_path}")
    return YOLO(model_path)


@app.get("/health")
def health() -> dict:
    model_path = os.getenv("CHASSIS_MODEL_PATH", "runs/chassis/yolo_chassis_parts/weights/best.pt")
    return {"ok": os.path.exists(model_path), "model_path": model_path}


@app.post("/detect", response_model=DetectResponse)
def detect(payload: DetectRequest) -> DetectResponse:
    image = _decode_image(payload.image_data_url)
    try:
        results = model().predict(
            image,
            imgsz=payload.image_size,
            conf=payload.confidence,
            iou=payload.iou,
            verbose=False,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    names = model().names
    detections: list[Detection] = []
    for result in results:
        for box in result.boxes:
            class_id = int(box.cls[0].item())
            x1, y1, x2, y2 = [int(value) for value in box.xyxy[0].tolist()]
            detections.append(
                Detection(
                    label=str(names.get(class_id, class_id)),
                    class_id=class_id,
                    confidence=round(float(box.conf[0].item()), 4),
                    bbox=[x1, y1, x2, y2],
                )
            )
    return DetectResponse(
        file_name=payload.file_name,
        detector_name="yolo_chassis_parts",
        image={"width": image.width, "height": image.height},
        detections=detections,
    )


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
