_base_ = ["mmdet::mask_rcnn/mask-rcnn_r50_fpn_1x_coco.py"]

data_root = "data/chassis_coco_seg/"
classes = (
    "upper_control_arm",
    "lower_control_arm",
    "front_subframe",
    "brake_disc",
    "brake_caliper",
    "steering_knuckle",
    "tie_rod",
    "drive_shaft",
)
metainfo = {"classes": classes}
num_classes = len(classes)

model = dict(
    roi_head=dict(
        bbox_head=dict(num_classes=num_classes),
        mask_head=dict(num_classes=num_classes),
    )
)

train_dataloader = dict(
    batch_size=2,
    num_workers=2,
    dataset=dict(
        type="CocoDataset",
        data_root=data_root,
        metainfo=metainfo,
        ann_file="annotations/train.json",
        data_prefix=dict(img="images/train/"),
    ),
)

val_dataloader = dict(
    batch_size=1,
    num_workers=2,
    dataset=dict(
        type="CocoDataset",
        data_root=data_root,
        metainfo=metainfo,
        ann_file="annotations/val.json",
        data_prefix=dict(img="images/val/"),
    ),
)

test_dataloader = dict(
    batch_size=1,
    num_workers=2,
    dataset=dict(
        type="CocoDataset",
        data_root=data_root,
        metainfo=metainfo,
        ann_file="annotations/test.json",
        data_prefix=dict(img="images/test/"),
    ),
)

val_evaluator = dict(ann_file=data_root + "annotations/val.json")
test_evaluator = dict(ann_file=data_root + "annotations/test.json")

train_cfg = dict(max_epochs=12, val_interval=1)
default_hooks = dict(checkpoint=dict(interval=1, max_keep_ckpts=2))

optim_wrapper = dict(
    optimizer=dict(type="AdamW", lr=0.0001, weight_decay=0.05),
)

param_scheduler = [
    dict(type="LinearLR", start_factor=0.001, by_epoch=False, begin=0, end=500),
    dict(type="MultiStepLR", by_epoch=True, begin=0, end=12, milestones=[8, 11], gamma=0.1),
]

auto_scale_lr = dict(base_batch_size=8)
