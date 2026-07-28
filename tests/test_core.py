import io
import unittest

from fastapi.testclient import TestClient
from PIL import Image

from app.annotate import make_annotated_data_url
from app.detectors import DemoChassisDetector
from app.main import app


class CoreTest(unittest.TestCase):
    def test_demo_detector_returns_candidate_parts(self) -> None:
        image = Image.new("RGB", (800, 600), "white")
        detections = DemoChassisDetector().detect(image)

        self.assertEqual(len(detections), 3)
        self.assertEqual(detections[0].label, "subframe")
        self.assertGreater(detections[0].confidence, 0)

    def test_annotation_returns_jpeg_data_url(self) -> None:
        image = Image.new("RGB", (800, 600), "white")
        detections = DemoChassisDetector().detect(image)

        data_url = make_annotated_data_url(image, detections)

        self.assertTrue(data_url.startswith("data:image/jpeg;base64,"))

    def test_analyze_endpoint_accepts_image(self) -> None:
        client = TestClient(app)
        image = Image.new("RGB", (640, 480), "white")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")

        response = client.post(
            "/api/analyze",
            files={"file": ("chassis.png", buffer.getvalue(), "image/png")},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["image"]["width"], 640)
        self.assertEqual(len(payload["objects"]), 3)
        self.assertTrue(payload["annotated_image"].startswith("data:image/jpeg;base64,"))


if __name__ == "__main__":
    unittest.main()
