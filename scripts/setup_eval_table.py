from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.runner import EVAL_EXTRA_FIELDS  # noqa: E402
from integrations.feishu_bitable import FeishuBitableClient, FeishuAPIError  # noqa: E402


def main() -> int:
    load_dotenv(ROOT / ".env")
    client = FeishuBitableClient.from_env()
    if not client.eval_table_id:
        raise FeishuAPIError("配置评测表", "缺少 FEISHU_EVAL_TABLE_ID")
    created = client.ensure_fields(table_id=client.eval_table_id, fields=EVAL_EXTRA_FIELDS)
    print("created=" + (",".join(created) if created else "none"))
    print(f"field_count={len(client.list_fields(table_id=client.eval_table_id))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
