from __future__ import annotations

import argparse
import shutil
import subprocess


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train chassis instance segmentation with MMDetection.")
    parser.add_argument("--config", default="vision_model/config/chassis_mask_rcnn_r50_fpn.py")
    parser.add_argument("--work-dir", default="work_dirs/chassis_mask_rcnn_r50_fpn")
    parser.add_argument("--load-from", default=None)
    parser.add_argument("--launcher", default="none")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    mim = shutil.which("mim")
    if mim is None:
        raise SystemExit("mim is not installed. Install openmim first: pip install -U openmim")

    cmd = [
        mim,
        "train",
        "mmdet",
        args.config,
        "--work-dir",
        args.work_dir,
        "--launcher",
        args.launcher,
    ]
    if args.load_from:
        cmd.extend(["--cfg-options", f"load_from={args.load_from}"])

    raise SystemExit(subprocess.call(cmd))


if __name__ == "__main__":
    main()
