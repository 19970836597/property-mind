"""Train the collection-policy artifact from local property Excel files."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import date, datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.paths import DATA_COMMON_DIR, OUTPUTS_DIR
from rl.config import load_config
from rl.loader import load_collection_dataset
from rl.storage import PolicyStore
from rl.timeutils import business_today
from rl.trainer import CollectionPolicyTrainer


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="训练物业催缴预算型 Bandit")
    parser.add_argument("--data-dir", default=str(DATA_COMMON_DIR))
    parser.add_argument("--as-of", help="账龄参考日 YYYY-MM-DD，默认今天")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--replicates", type=int, default=8)
    parser.add_argument(
        "--profiles",
        default="conservative,base,optimistic",
        help="逗号分隔的效果场景",
    )
    parser.add_argument("--output-root", default=str(OUTPUTS_DIR / "rl"))
    parser.add_argument(
        "--no-publish",
        action="store_true",
        help="只写入 run 目录，不更新 collection_policy_v1.json",
    )
    return parser.parse_args()


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass
    args = parse_args()
    as_of = date.fromisoformat(args.as_of) if args.as_of else business_today()
    profiles = [item.strip() for item in args.profiles.split(",") if item.strip()]
    config = load_config()
    dataset = load_collection_dataset(
        args.data_dir,
        as_of=as_of,
        config=config,
    )
    artifact = CollectionPolicyTrainer(config).fit(
        dataset,
        profiles=profiles,
        seed=args.seed,
        replicates=args.replicates,
    )

    run_id = datetime.now(timezone.utc).strftime("run_%Y%m%dT%H%M%SZ")
    run_dir = Path(args.output_root) / run_id
    policy_path = run_dir / "collection_policy_v1.json"
    PolicyStore.save(artifact, policy_path)
    (run_dir / "config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (run_dir / "dataset_metadata.json").write_text(
        json.dumps(dataset.metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    latest = Path(args.output_root) / "collection_policy_v1.json"
    if not args.no_publish:
        shutil.copyfile(policy_path, latest)

    print(f"策略版本: {artifact.version}")
    print(f"训练案例: {len(dataset.cases)}")
    print(f"样本数: {artifact.sample_counts}")
    print(f"策略文件: {policy_path}")
    if not args.no_publish:
        print(f"最新策略: {latest}")
    print("提醒: 当前动作效果包含业务先验，并非真实因果效果。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
