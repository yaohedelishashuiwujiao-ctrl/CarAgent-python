# MMDetection 训练与推理

This project uses MMDetection for the instance segmentation path.

## 1. Install

Create a clean GPU environment, install PyTorch first, then:

```bash
pip install -U openmim
mim install mmengine
mim install "mmcv>=2.0.0"
mim install mmdet
```

MMDetection official docs recommend the same flow and provide a quick inference verification using `mim download mmdet` and `init_detector` / `inference_detector`.

## 2. Export dataset from the platform

Use the platform's COCO export endpoint:

```bash
/api/datasets/exports/coco-seg.zip
```

The archive contains:

- `images/train`, `images/val`, `images/test`
- `annotations/train.json`, `annotations/val.json`, `annotations/test.json`
- `metainfo.json`

## 3. Fine-tune

Start from a pretrained instance segmentation checkpoint and set `load_from` in the MMDetection config.
For the first baseline, this repo includes a `Mask R-CNN R50-FPN` template at:

`vision_model/config/chassis_mask_rcnn_r50_fpn.py`

Suggested first run on an 8 GB GPU:

```bash
python vision_model/train_mmdet.py --config vision_model/config/chassis_mask_rcnn_r50_fpn.py --work-dir work_dirs/chassis_mask_rcnn_r50_fpn
```

If you want to pin a pretrained checkpoint explicitly, pass `load_from` through config or CLI override. The key point is to fine-tune from pretrained weights, not train from scratch.

## 4. Serve inference

Run `vision_model/mmdet_infer_service.py` with:

- `MMDET_CONFIG_FILE`
- `MMDET_CHECKPOINT_FILE`
- `MMDET_DEVICE`

Example:

```bash
MMDET_CONFIG_FILE=... MMDET_CHECKPOINT_FILE=... MMDET_DEVICE=cuda:0 uvicorn vision_model.mmdet_infer_service:app --host 0.0.0.0 --port 8010
```

Then point the platform backend to it with:

```bash
VISION_SEGMENTATION_URL=http://127.0.0.1:8010/detect
```
