from __future__ import annotations

import os

import requests

from backend.app.config import settings
from backend.app.schemas_runtime import RuntimeCapability, RuntimeStatus


class RuntimeService:
    def status(self) -> RuntimeStatus:
        capabilities = [
            self._capability(
                key="evidence",
                name="证据层",
                status=self._repository_status(),
                level="production_required",
                detail=(
                    f"当前 DATA_BACKEND={settings.data_backend}。证据中心 API 已支持 memory/mysql Repository；"
                    "结构化资产会投影为证据，手工/采集证据会写入 evidence_item。"
                ),
            ),
            self._capability(
                key="metadata",
                name="动态元数据",
                status=self._repository_status(),
                level="production_required",
                detail=(
                    f"当前 DATA_BACKEND={settings.data_backend}。动态元数据 API 已支持 memory/mysql Repository，"
                    "包括实体类型、虚拟系统、字段分组和动态字段。"
                ),
            ),
            self._capability(
                key="dataset",
                name="数据集标注",
                status=self._repository_status(),
                level="production_required",
                detail=(
                    f"当前 DATA_BACKEND={settings.data_backend}。图片池、bbox 标注、类别统计和 YOLO 导出计划"
                    "已支持 memory/mysql Repository。"
                ),
            ),
            self._capability(
                key="vision_tasks",
                name="视觉任务记录",
                status=self._repository_status(),
                level="production_required",
                detail="视觉识别任务已支持 memory/mysql Repository；当前推理链路支持 MMDetection / YOLO / demo 三种模式。",
            ),
            self._capability(
                key="collector_tasks",
                name="采集任务",
                status=self._repository_status(),
                level="production_required",
                detail="线上采集任务已支持 memory/mysql Repository；采集结果应进入 evidence_item 等待人工复核。",
            ),
            self._capability(
                key="rbac",
                name="权限 RBAC",
                status=self._repository_status(),
                level="production_required",
                detail="角色、权限和角色权限关系已支持 memory/mysql Repository。",
            ),
            self._capability(
                key="mysql",
                name="MySQL 持久化",
                status=self._mysql_status(),
                level="production_required",
                detail=(
                    f"当前 DATA_BACKEND={settings.data_backend}。设置 DATA_BACKEND=mysql 和 DATABASE_URL 后，"
                    "数据资产 API 会走 MySQL Repository。"
                ),
            ),
            self._capability(
                key="redis",
                name="Redis 缓存/任务状态",
                status=self._redis_status(),
                level="production_required",
                detail="Redis 用于热点聚合缓存和后续异步任务状态；车型来源统计已接入短 TTL 缓存。",
            ),
            self._capability(
                key="langchain",
                name="Agent 工具链",
                status="ready" if self._has_langchain() else "missing",
                level="mvp",
                detail="依赖已安装时可进入工具编排增强模式；模型调用走 Ark OpenAI 兼容接口。",
            ),
            self._capability(
                key="ark",
                name="Ark 模型调用",
                status="ready" if (os.getenv("OPENAI_API_KEY") or os.getenv("ARK_API_KEY")) else "missing",
                level="production_required",
                detail="配置 ARK_API_KEY 后启用真实模型生成、工具调用和报告润色。",
            ),
            self._capability(
                key="vision",
                name="底盘视觉识别",
                status=self._vision_status(),
                level="research_required",
                detail=(
                    f"当前 VISION_SEGMENTATION_URL={settings.vision_segmentation_url or 'unset'}，"
                    f"VISION_DETECTOR_URL={settings.vision_detector_url or 'unset'}。"
                    "后端优先调用远端 MMDetection 实例分割服务，其次回退到 YOLO 服务；若都不可用则回退到 demo detector。"
                ),
            ),
        ]
        warnings = [item.detail for item in capabilities if item.status in {"missing", "demo", "degraded"}]
        return RuntimeStatus(
            service="chassis-benchmark-platform",
            environment=settings.app_env,
            capabilities=capabilities,
            warnings=warnings,
            vision_backend=self._vision_backend_snapshot(),
        )

    def _capability(self, key: str, name: str, status: str, level: str, detail: str) -> RuntimeCapability:
        return RuntimeCapability(key=key, name=name, status=status, level=level, detail=detail)

    def _has_langchain(self) -> bool:
        try:
            import langchain  # noqa: F401
        except ImportError:
            return False
        return True

    def _mysql_status(self) -> str:
        if settings.data_backend.lower() != "mysql":
            return "missing"
        if not settings.database_url:
            return "missing"
        try:
            from backend.app.db import mysql_connection

            with mysql_connection() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1 AS ok")
                    cursor.fetchone()
        except ImportError:
            return "missing"
        except Exception:
            return "degraded"
        return "ready"

    def _repository_status(self) -> str:
        if settings.data_backend.lower() == "memory":
            return "ready"
        return self._mysql_status()

    def _redis_status(self) -> str:
        if not settings.redis_url:
            return "missing"
        try:
            import redis

            client = redis.Redis.from_url(settings.redis_url, socket_connect_timeout=0.3, socket_timeout=0.3)
            client.ping()
        except ImportError:
            return "missing"
        except Exception:
            return "degraded"
        return "ready"

    def _vision_status(self) -> str:
        segmentation_ready = self._health_ok(settings.vision_segmentation_url)
        detector_ready = self._health_ok(settings.vision_detector_url)
        if segmentation_ready:
            return "ready"
        if detector_ready:
            return "ready"
        if settings.vision_segmentation_url or settings.vision_detector_url:
            return "degraded"
        return "demo"

    def _health_ok(self, url: str | None) -> bool:
        if not url:
            return False
        try:
            health_url = url.rsplit("/detect", 1)[0] + "/health"
            response = requests.get(health_url, timeout=3)
            response.raise_for_status()
            payload = response.json()
            return bool(payload.get("ok"))
        except Exception:
            return False

    def _vision_backend_snapshot(self) -> dict[str, str]:
        primary = settings.vision_segmentation_url or ""
        secondary = settings.vision_detector_url or ""
        if self._health_ok(primary):
            active = "mmdet"
        elif self._health_ok(secondary):
            active = "yolo"
        else:
            active = "demo"
        return {
            "active": active,
            "primary": primary or "unset",
            "secondary": secondary or "unset",
        }


runtime_service = RuntimeService()
