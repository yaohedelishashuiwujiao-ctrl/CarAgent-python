from __future__ import annotations

import base64
import io
from dataclasses import dataclass
from functools import lru_cache

import requests
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError

from backend.app.config import settings
from backend.app.db import DatabaseUnavailable
from backend.app.repositories.metadata import MemoryMetadataRepository, MySqlMetadataRepository
from backend.app.repositories.vision import MemoryVisionRepository, MySqlVisionRepository, VisionRepository
from backend.app.schemas_vision import (
    VisionAnalyzeRequest,
    VisionAnalyzeResponse,
    VisionDetection,
    VisionRefineRequest,
    VisionRefineResponse,
    VisionTask,
)


@dataclass(frozen=True)
class Candidate:
    entity_type_code: str
    confidence: float
    bbox_ratio: tuple[float, float, float, float]
    reasoning: str
    source: str = "model"
    polygon: list[int] | None = None


class DemoChassisVisionDetector:
    name = "demo_detector"

    def detect(self, image: Image.Image, confidence: float = 0.25, iou: float = 0.7, image_size: int = 960) -> list[Candidate]:
        width, height = image.size
        if width < 120 or height < 120:
            return []
        return [
            Candidate(
                entity_type_code="upper_control_arm",
                confidence=0.64,
                bbox_ratio=(0.18, 0.46, 0.48, 0.64),
                reasoning="位于悬架区域，形状和安装位置接近上控制臂；当前为演示 detector，必须人工复核。",
            ),
            Candidate(
                entity_type_code="front_subframe",
                confidence=0.58,
                bbox_ratio=(0.34, 0.36, 0.74, 0.58),
                reasoning="横向承载结构疑似副车架；遮挡和角度会显著影响判断。",
            ),
            Candidate(
                entity_type_code="drive_shaft",
                confidence=0.52,
                bbox_ratio=(0.48, 0.52, 0.82, 0.62),
                reasoning="细长轴状结构疑似半轴；需要结合车辆方向和动力系统位置确认。",
            ),
        ]


class RemoteChassisVisionDetector:
    def __init__(self, detector_url: str) -> None:
        self.detector_url = detector_url
        self.name = "remote_yolo_detector"

    def detect(self, image: Image.Image, confidence: float = 0.25, iou: float = 0.7, image_size: int = 960) -> list[Candidate]:
        payload = {
            "file_name": "analyze.jpg",
            "image_data_url": _encode_image(image),
            "confidence": confidence,
            "iou": iou,
            "image_size": image_size,
        }
        response = requests.post(self.detector_url, json=payload, timeout=60)
        response.raise_for_status()
        body = response.json()
        candidates: list[Candidate] = []
        for item in body.get("detections", []):
            bbox = item.get("bbox", [0, 0, 0, 0])
            candidates.append(
                Candidate(
                    entity_type_code=str(item.get("label", "")),
                    confidence=float(item.get("confidence", 0.0)),
                    bbox_ratio=(
                        max(0.0, min(1.0, bbox[0] / image.width)),
                        max(0.0, min(1.0, bbox[1] / image.height)),
                        max(0.0, min(1.0, bbox[2] / image.width)),
                        max(0.0, min(1.0, bbox[3] / image.height)),
                    ),
                    reasoning=f"Remote YOLO detector {body.get('detector_name', 'unknown')} inference result.",
                    source="model",
                    polygon=[int(value) for value in item.get("polygon", [])] if item.get("polygon") else None,
                )
            )
        return candidates


class RemoteChassisSegmentationDetector:
    def __init__(self, detector_url: str) -> None:
        self.detector_url = detector_url
        self.name = "remote_mmdet_detector"

    def detect(self, image: Image.Image, confidence: float = 0.25, iou: float = 0.7, image_size: int = 960) -> list[Candidate]:
        payload = {
            "file_name": "analyze.jpg",
            "image_data_url": _encode_image(image),
            "confidence": confidence,
            "iou": iou,
            "image_size": image_size,
        }
        response = requests.post(self.detector_url, json=payload, timeout=90)
        response.raise_for_status()
        body = response.json()
        candidates: list[Candidate] = []
        for item in body.get("detections", []):
            bbox = item.get("bbox", [0, 0, 0, 0])
            candidates.append(
                Candidate(
                    entity_type_code=str(item.get("label", "")),
                    confidence=float(item.get("confidence", 0.0)),
                    bbox_ratio=(
                        max(0.0, min(1.0, bbox[0] / image.width)),
                        max(0.0, min(1.0, bbox[1] / image.height)),
                        max(0.0, min(1.0, bbox[2] / image.width)),
                        max(0.0, min(1.0, bbox[3] / image.height)),
                    ),
                    reasoning=f"Remote MMDetection detector {body.get('detector_name', 'unknown')} inference result.",
                    source="model",
                    polygon=[int(value) for value in item.get("polygon", [])] if item.get("polygon") else None,
                )
            )
        return candidates


class RegionProposalDetector:
    name = "region_proposal"

    def detect(self, image: Image.Image, confidence: float = 0.25, iou: float = 0.7, image_size: int = 960) -> list[Candidate]:
        rgb = np.array(image.convert("RGB"))
        saliency = cv2.saliency.StaticSaliencySpectralResidual_create()
        ok, saliency_map = saliency.computeSaliency(rgb)
        if not ok:
            return []

        saliency_map = (np.clip(saliency_map, 0.0, 1.0) * 255).astype("uint8")
        kernels = [
            cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5)),
            cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9)),
        ]
        image_area = image.width * image.height
        candidates: list[tuple[float, tuple[int, int, int, int], str]] = []

        for threshold_ratio in (0.92, 0.9, 0.88):
            threshold = int(np.quantile(saliency_map, threshold_ratio))
            _, binary = cv2.threshold(saliency_map, threshold, 255, cv2.THRESH_BINARY)
            for kernel in kernels:
                binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
                binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)
            contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for contour in contours:
                x, y, w, h = cv2.boundingRect(contour)
                area = w * h
                if area < image_area * 0.01 or area > image_area * 0.75:
                    continue
                if w < 24 or h < 24:
                    continue
                aspect = max(w / max(h, 1), h / max(w, 1))
                if aspect > 12:
                    continue
                mask = np.zeros_like(saliency_map, dtype="uint8")
                cv2.drawContours(mask, [contour], -1, 255, thickness=-1)
                mean_saliency = float(cv2.mean(saliency_map, mask=mask)[0]) / 255.0
                candidate_confidence = max(0.25, min(0.82, 0.35 + mean_saliency * 0.5))
                candidates.append((candidate_confidence, (x, y, x + w, y + h), "未能稳定识别具体零部件名称，但已提取到疑似零部件区域，建议人工定类后入库。"))

        if not candidates:
            gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
            blurred = cv2.GaussianBlur(gray, (5, 5), 0)
            edges = cv2.Canny(blurred, 30, 120)
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7))
            dilated = cv2.dilate(edges, kernel, iterations=2)
            closed = cv2.morphologyEx(dilated, cv2.MORPH_CLOSE, kernel, iterations=2)
            contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for contour in contours:
                x, y, w, h = cv2.boundingRect(contour)
                area = w * h
                if area < image_area * 0.01 or area > image_area * 0.75:
                    continue
                if w < 24 or h < 24:
                    continue
                aspect = max(w / max(h, 1), h / max(w, 1))
                if aspect > 12:
                    continue
                fill_ratio = min(1.0, cv2.contourArea(contour) / float(area or 1))
                candidate_confidence = max(0.25, min(0.72, 0.3 + fill_ratio * 0.4))
                candidates.append((candidate_confidence, (x, y, x + w, y + h), "未能稳定识别具体零部件名称，但依据边缘和连通区域提取到疑似零部件区域。"))

        deduped: list[tuple[float, tuple[int, int, int, int], str]] = []
        for confidence_value, box, reasoning in sorted(candidates, key=lambda item: item[0], reverse=True):
            if any(_iou(box, existing_box) > 0.65 for _, existing_box, _ in deduped):
                continue
            deduped.append((confidence_value, box, reasoning))
            if len(deduped) >= 5:
                break

        return [
            Candidate(
                entity_type_code="unknown_part",
                confidence=round(confidence_value, 4),
                bbox_ratio=(box[0] / image.width, box[1] / image.height, box[2] / image.width, box[3] / image.height),
                reasoning=reasoning,
                source="region_proposal",
            )
            for confidence_value, box, reasoning in deduped
        ]


class VisionService:
    def __init__(self) -> None:
        self.detector = get_detector()
        self.repository = get_vision_repository()
        self.metadata_repository = MySqlMetadataRepository() if settings.data_backend.lower() == "mysql" else MemoryMetadataRepository()

    @property
    def tasks(self) -> list[VisionTask]:
        return self.repository.list_tasks()

    def analyze(self, payload: VisionAnalyzeRequest) -> VisionAnalyzeResponse:
        image = self._decode_image(payload.image_data_url)
        candidates = self.detector.detect(image, payload.confidence, payload.iou, payload.image_size)
        if not candidates:
            candidates = RegionProposalDetector().detect(image, payload.confidence, payload.iou, payload.image_size)
        detections = [self._candidate_to_detection(index + 1, image, candidate) for index, candidate in enumerate(candidates)]
        annotated_image = self._annotate(image, detections)
        summary = self._summarize(detections, self.detector.name)
        task = self.repository.create_task(
            file_name=payload.file_name,
            detector_name=self.detector.name,
            object_count=len(detections),
            ai_summary=summary,
            metadata={"vehicle_instance_id": payload.vehicle_instance_id, "note": payload.note},
        )
        return VisionAnalyzeResponse(
            task=task,
            image={"width": image.width, "height": image.height},
            detections=detections,
            annotated_image=annotated_image,
            ai_summary=summary,
        )

    def refine(self, payload: VisionRefineRequest) -> VisionRefineResponse:
        image = self._decode_image(payload.image_data_url)
        polygon, mask_coverage, annotated_image = self._refine_with_grabcut(image, payload.bbox, payload.iterations)
        return VisionRefineResponse(
            file_name=payload.file_name,
            bbox=payload.bbox,
            polygon=polygon,
            annotated_image=annotated_image,
            mask_coverage=round(mask_coverage, 4),
            ai_summary="已根据粗框自动贴合零部件轮廓，建议人工复核后保存为分割标注。",
        )

    def _candidate_to_detection(self, detection_id: int, image: Image.Image, candidate: Candidate) -> VisionDetection:
        entity_type = next((item for item in self.metadata_repository.list_entity_types("component") if item.code == candidate.entity_type_code), None)
        system = next((item for item in self.metadata_repository.list_systems() if entity_type and item.id == entity_type.default_system_id), None)
        x1, y1, x2, y2 = candidate.bbox_ratio
        bbox = [
            int(image.width * x1),
            int(image.height * y1),
            int(image.width * x2),
            int(image.height * y2),
        ]
        return VisionDetection(
            id=detection_id,
            entity_type_id=entity_type.id if entity_type else None,
            entity_type_code=candidate.entity_type_code,
            label=entity_type.name if entity_type else ("疑似零部件区域" if candidate.entity_type_code == "unknown_part" else candidate.entity_type_code),
            system_id=system.id if system else None,
            system_name=system.name if system else None,
            confidence=round(candidate.confidence, 4),
            bbox=bbox,
            polygon=candidate.polygon,
            source=candidate.source if candidate.source != "model" else self.detector.name,
            review_status="proposed" if candidate.source == "region_proposal" else ("needs_review" if candidate.confidence < 0.8 else "auto_accepted"),
            reasoning=candidate.reasoning,
        )

    def _decode_image(self, data_url: str) -> Image.Image:
        if "," not in data_url:
            raise ValueError("invalid image data url")
        _, encoded = data_url.split(",", 1)
        try:
            raw = base64.b64decode(encoded)
            image = Image.open(io.BytesIO(raw))
            image.load()
        except (OSError, ValueError, UnidentifiedImageError) as exc:
            raise ValueError("unsupported or invalid image") from exc
        return image.convert("RGB")

    def _annotate(self, image: Image.Image, detections: list[VisionDetection]) -> str:
        palette = ["#0f766e", "#2563eb", "#b45309", "#be123c", "#4d7c0f"]
        annotated = image.copy()
        draw = ImageDraw.Draw(annotated, "RGBA")
        font = ImageFont.load_default()
        for index, detection in enumerate(detections):
            color = palette[index % len(palette)]
            x1, y1, x2, y2 = detection.bbox
            label = f"{detection.label} {detection.confidence:.2f}"
            if detection.polygon and len(detection.polygon) >= 6:
                polygon_points = [(detection.polygon[idx], detection.polygon[idx + 1]) for idx in range(0, len(detection.polygon), 2)]
                draw.polygon(polygon_points, outline=color, fill=(0, 0, 0, 0))
            draw.rectangle((x1, y1, x2, y2), outline=color, width=4)
            text_box = draw.textbbox((x1, y1), label, font=font)
            text_width = text_box[2] - text_box[0]
            text_height = text_box[3] - text_box[1]
            label_top = max(0, y1 - text_height - 8)
            draw.rectangle((x1, label_top, x1 + text_width + 10, label_top + text_height + 8), fill=color)
            draw.text((x1 + 5, label_top + 4), label, fill="white", font=font)
        buffer = io.BytesIO()
        annotated.save(buffer, format="JPEG", quality=90)
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        return f"data:image/jpeg;base64,{encoded}"

    def _refine_with_grabcut(self, image: Image.Image, bbox: list[int], iterations: int) -> tuple[list[int], float, str]:
        if len(bbox) != 4:
            raise ValueError("bbox must contain [x1, y1, x2, y2]")
        x1, y1, x2, y2 = [int(v) for v in bbox]
        if x2 <= x1 or y2 <= y1:
            raise ValueError("invalid bbox coordinates")
        x1 = max(0, min(image.width - 1, x1))
        y1 = max(0, min(image.height - 1, y1))
        x2 = max(1, min(image.width, x2))
        y2 = max(1, min(image.height, y2))
        rect = (x1, y1, max(1, x2 - x1), max(1, y2 - y1))
        rgb = np.array(image.convert("RGB"))
        mask = np.zeros(rgb.shape[:2], np.uint8)
        bgd_model = np.zeros((1, 65), np.float64)
        fgd_model = np.zeros((1, 65), np.float64)
        try:
            cv2.grabCut(rgb, mask, rect, bgd_model, fgd_model, max(1, min(10, int(iterations))), cv2.GC_INIT_WITH_RECT)
            mask2 = np.where((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 1, 0).astype("uint8")
        except Exception:
            mask2 = np.zeros(rgb.shape[:2], dtype="uint8")
            mask2[y1:y2, x1:x2] = 1

        coverage = float(mask2.mean())
        if coverage <= 0.001:
            mask2 = np.zeros(rgb.shape[:2], dtype="uint8")
            mask2[y1:y2, x1:x2] = 1
            coverage = float(mask2.mean())

        contours, _ = cv2.findContours(mask2, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            polygon = [x1, y1, x2, y1, x2, y2, x1, y2]
        else:
            contour = max(contours, key=cv2.contourArea)
            perimeter = cv2.arcLength(contour, True)
            epsilon = 0.02 * perimeter if perimeter > 0 else 1.0
            approx = cv2.approxPolyDP(contour, epsilon, True)
            if len(approx) < 3:
                x, y, w, h = cv2.boundingRect(contour)
                polygon = [x, y, x + w, y, x + w, y + h, x, y + h]
            else:
                polygon = []
                for point in approx.reshape(-1, 2):
                    polygon.extend([int(point[0]), int(point[1])])

        annotated = image.copy()
        draw = ImageDraw.Draw(annotated, "RGBA")
        draw.rectangle((x1, y1, x2, y2), outline="#b45309", width=3)
        if len(polygon) >= 6:
            points = [(polygon[index], polygon[index + 1]) for index in range(0, len(polygon), 2)]
            draw.polygon(points, outline="#2563eb", fill=(37, 99, 235, 48))
        buffer = io.BytesIO()
        annotated.save(buffer, format="JPEG", quality=90)
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        return polygon, coverage, f"data:image/jpeg;base64,{encoded}"

    def _summarize(self, detections: list[VisionDetection], detector_name: str) -> str:
        if not detections:
            return "未检测到明确底盘零部件。建议检查图片是否为底盘区域、是否过暗、是否模糊或遮挡严重。"
        names = "、".join(item.label for item in detections)
        low_conf = [item.label for item in detections if item.confidence < 0.7]
        has_region_proposal = any(item.source == "region_proposal" for item in detections)
        suffix = "低置信度结果需要人工确认。" if low_conf else "结果仍建议由数据维护员复核后入库。"
        if has_region_proposal and all(item.entity_type_code == "unknown_part" for item in detections):
            return f"图中未能稳定识别具体零部件名称，但已圈出 {len(detections)} 个疑似区域。建议人工定类后入库。"
        detector_lower = detector_name.lower()
        if "mmdet" in detector_lower or "seg" in detector_lower:
            return f"图中疑似包含 {names}。当前结果由实例分割模型产生；{suffix}"
        if "yolo" in detector_lower:
            return f"图中疑似包含 {names}。当前结果由实际 YOLO 模型产生；{suffix}"
        return f"图中疑似包含 {names}。当前结果由演示 detector 产生，只用于验证产品流程；{suffix}"


@lru_cache(maxsize=1)
def get_vision_repository() -> VisionRepository:
    backend = settings.data_backend.lower()
    if backend == "memory":
        return MemoryVisionRepository()
    if backend == "mysql":
        if not settings.database_url:
            raise DatabaseUnavailable("DATA_BACKEND=mysql requires DATABASE_URL")
        return MySqlVisionRepository()
    raise DatabaseUnavailable(f"unsupported DATA_BACKEND: {settings.data_backend}")


@lru_cache(maxsize=1)
def get_detector():
    if settings.vision_segmentation_url:
        return RemoteChassisSegmentationDetector(settings.vision_segmentation_url)
    if settings.vision_detector_url:
        return RemoteChassisVisionDetector(settings.vision_detector_url)
    return DemoChassisVisionDetector()


def _encode_image(image: Image.Image) -> str:
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=90)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def _iou(box_a: tuple[int, int, int, int], box_b: tuple[int, int, int, int]) -> float:
    ax1, ay1, ax2, ay2 = box_a
    bx1, by1, bx2, by2 = box_b
    inter_x1 = max(ax1, bx1)
    inter_y1 = max(ay1, by1)
    inter_x2 = min(ax2, bx2)
    inter_y2 = min(ay2, by2)
    inter_w = max(0, inter_x2 - inter_x1)
    inter_h = max(0, inter_y2 - inter_y1)
    inter_area = inter_w * inter_h
    if inter_area == 0:
        return 0.0
    area_a = max(0, ax2 - ax1) * max(0, ay2 - ay1)
    area_b = max(0, bx2 - bx1) * max(0, by2 - by1)
    union = area_a + area_b - inter_area
    if union <= 0:
        return 0.0
    return inter_area / union


vision_service = VisionService()
