import unittest

from fastapi.testclient import TestClient

from backend.app.main import app


class KnowledgeApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_status_returns_pipeline_snapshot(self) -> None:
        response = self.client.get("/api/knowledge/status")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["snapshot_name"], "manual-corpus")
        metric_labels = {item["label"] for item in payload["metrics"]}
        self.assertIn("中国市场来源", metric_labels)
        self.assertTrue(payload["stages"])
        self.assertTrue(payload["source_samples"])
        self.assertTrue(payload["artifact_samples"])

    def test_search_finds_real_manual_text(self) -> None:
        response = self.client.get("/api/knowledge/test", params={"query": "遥控泊车", "top_k": 3})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertGreater(payload["total_matches"], 0)
        self.assertTrue(payload["hits"])
        excerpt_text = payload["hits"][0]["excerpt"]
        self.assertTrue("遥控泊车" in excerpt_text or "泊车" in excerpt_text)

    def test_rag_api_returns_fetchable_chunks(self) -> None:
        response = self.client.post("/api/rag/search", json={"query": "遥控泊车", "top_k": 1})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["provider"], "platform")
        self.assertTrue(payload["results"])
        chunk_id = payload["results"][0]["chunk_id"]

        fetched = self.client.get(f"/api/rag/chunks/{chunk_id}")
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(fetched.json()["chunk_id"], chunk_id)


if __name__ == "__main__":
    unittest.main()
