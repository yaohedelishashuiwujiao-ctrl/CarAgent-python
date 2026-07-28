# 存储后端设计

## 当前策略

后端支持两种数据资产存储模式：

```text
DATA_BACKEND=memory
DATA_BACKEND=mysql
```

默认使用 `memory`，便于本地快速开发；生产或准生产环境应使用 `mysql`。

## 配置

```bash
DATA_BACKEND=mysql
DATABASE_URL=mysql+pymysql://chassis:chassis_dev_password@127.0.0.1:3306/chassis_platform
REDIS_URL=redis://127.0.0.1:6380/0
```

## 已接入 MySQL Repository 的接口

### 数据资产

- `GET /api/assets/vehicles`
- `POST /api/assets/vehicles`
- `GET /api/assets/system-profiles`
- `GET /api/assets/components`
- `POST /api/assets/components`
- `GET /api/assets/tree`

这些接口通过 `backend/app/services/assets.py` 选择 Repository。

### 动态元数据

- `GET /api/metadata/entity-types`
- `POST /api/metadata/entity-types`
- `PUT /api/metadata/entity-types/{entity_type_id}`
- `GET /api/metadata/systems`
- `GET /api/metadata/attribute-groups`
- `GET /api/metadata/attributes`
- `POST /api/metadata/attributes`
- `PUT /api/metadata/attributes/{attribute_id}`

这些接口通过 `backend/app/services/metadata.py` 选择 Repository。

### 证据中心

- `GET /api/evidence/items`
- `POST /api/evidence/items`
- `GET /api/evidence/summary`

这些接口通过 `backend/app/services/evidence.py` 选择 Repository。

证据中心采用“投影证据 + 持久证据”组合：

- 车型、零部件实例、图片池、人工标注、视觉任务会投影成 Evidence，用于 Agent 检索。
- 手工调研、线上采集、人审结论写入 `evidence_item`。
- Agent 只从 Evidence 层检索，不直接信任原始爬虫或视觉结果。

Repository 实现：

- `MemoryAssetRepository`
- `MySqlAssetRepository`
- `MemoryMetadataRepository`
- `MySqlMetadataRepository`
- `MemoryEvidenceRepository`
- `MySqlEvidenceRepository`
- `MemoryDatasetRepository`
- `MySqlDatasetRepository`
- `MemoryVisionRepository`
- `MySqlVisionRepository`
- `MemoryCollectorRepository`
- `MySqlCollectorRepository`
- `MemoryPermissionRepository`
- `MySqlPermissionRepository`

### 数据集标注

- `GET /api/datasets/images`
- `POST /api/datasets/images`
- `GET /api/datasets/annotations`
- `POST /api/datasets/annotations`
- `GET /api/datasets/summary`
- `GET /api/datasets/exports/yolo-plan`

图片池、bbox 标注、类别覆盖统计和 YOLO 导出计划都已支持 MySQL Repository。当前图片内容可暂存在 `dataset_image.image_data_url`，正式部署建议切换到 MinIO/S3，只在数据库保留对象存储 URL 和元数据。

### 视觉任务

- `POST /api/vision/analyze`
- `GET /api/vision/tasks`

视觉任务记录已支持 MySQL Repository。当前 detector 仍为 `demo_detector`，后续替换为正式 YOLO/RT-DETR 推理服务时，应保持接口返回结构稳定。

### 采集任务

- `GET /api/collectors/tasks`
- `POST /api/collectors/tasks`

采集任务已支持 MySQL Repository。采集出的事实、图片链接和网页摘要不直接写正式属性，应先进入 `evidence_item`。

### 权限 RBAC

- `GET /api/permissions/roles`
- `GET /api/permissions`

角色、权限和角色权限关系已支持 MySQL Repository。

## 数据初始化

Docker Compose 会执行：

```text
database/schema.sql
database/seed.sql
```

两个 SQL 文件顶部必须保留：

```sql
SET NAMES utf8mb4;
```

原因是 MySQL 官方镜像初始化 SQL 时 client 可能不是 utf8mb4；不显式设置会导致中文 seed 数据被错误写入。

## 验证

启动 MySQL：

```bash
docker compose up -d mysql redis
```

启动后端：

```bash
DATA_BACKEND=mysql \
DATABASE_URL=mysql+pymysql://chassis:chassis_dev_password@127.0.0.1:3306/chassis_platform \
python3 -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

检查：

```bash
curl http://127.0.0.1:8000/api/assets/vehicles
curl http://127.0.0.1:8000/api/assets/tree
curl http://127.0.0.1:8000/api/runtime/status
```

`/api/runtime/status` 会真实 ping MySQL/Redis，不只检查环境变量。

## 后续迁移顺序

建议按业务依赖顺序迁移：

1. 导入导出任务 Repository：`import_job`、`import_job_item`、`export_job`。
2. Agent 上下文缓存：MySQL + Redis，后续再按需要引入向量索引或搜索服务。
3. 正式视觉模型服务：YOLO/RT-DETR 训练、模型注册、推理任务队列和人工复核闭环。
