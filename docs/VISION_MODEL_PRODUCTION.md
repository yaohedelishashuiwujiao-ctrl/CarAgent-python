# 底盘零部件检测模型生产化方案

## 目标

把当前 `demo_detector` 替换为正式底盘零部件检测模型：

```text
数据集标注平台
  -> 导出 YOLO 数据集
  -> RTX 5060 Ti 服务器训练
  -> 推理服务
  -> 平台视觉识别 API
  -> 低置信度结果进入证据层和人工复核
```

## 服务器

当前实际训练和推理服务器：

```text
192.168.1.2
GPU: RTX 5060 Ti 8GB
```

训练和推理都已经在这台机器上验证通过。

## 数据目录

默认 YOLO 数据目录：

```text
/data/chassis_yolo/
  images/
    train/
    val/
    test/
  labels/
    train/
    val/
    test/
```

数据配置：

```text
vision_model/config/chassis_parts.yaml
```

## 类别

当前第一版类别：

```text
0 upper_control_arm
1 lower_control_arm
2 front_subframe
3 brake_disc
4 brake_caliper
5 steering_knuckle
6 tie_rod
7 drive_shaft
```

后续应从动态元数据导出 class map，并按模型版本冻结。

## 安装

在服务器上：

```bash
cd ~/SubjectsDetection
bash scripts/setup_vision_server.sh
```

默认安装 PyTorch CUDA 12.8 wheel：

```bash
TORCH_INDEX_URL=https://download.pytorch.org/whl/cu128
```

如果服务器驱动/CUDA 组合不匹配，覆盖 `TORCH_INDEX_URL`。

## 训练

```bash
source .venv-vision/bin/activate
python vision_model/train_yolo.py \
  --data vision_model/config/chassis_parts.yaml \
  --model yolo11s.pt \
  --epochs 100 \
  --imgsz 960 \
  --batch 8 \
  --device 0
```

8GB 显存建议从 `yolo11s.pt`、`imgsz=960`、`batch=8` 起步；显存不足时降到 `batch=4` 或 `imgsz=768`。

## 推理服务

```bash
CHASSIS_MODEL_PATH=$PWD/runs/chassis/yolo_chassis_parts/weights/best.pt \
bash scripts/run_vision_service.sh
```

默认端口：

```text
http://0.0.0.0:8010
```

接口：

```text
GET  /health
POST /detect
```

## 平台集成

当前平台的 `/api/vision/analyze` 已接入远端 YOLO 服务：

1. 环境变量 `VISION_DETECTOR_URL=http://192.168.1.2:8010/detect`
2. 后端优先调用远端 YOLO 服务
3. 远端服务不可用时才回退到 demo detector
4. 检测结果写入 `vision_task`，低置信度结果进入 `evidence_item`，等待人工复核

## 数据质量门槛

正式版本最低要求：

- 每类至少 300 个高质量 bbox，推荐 800+。
- 每类 train/val/test 都有覆盖。
- 保留遮挡、不同角度、维修图、底盘仰拍、局部特写。
- 所有自动采集图片必须人工确认授权和标注质量。
- 每次训练固化：数据版本、class map、训练参数、模型权重、评估结果。
