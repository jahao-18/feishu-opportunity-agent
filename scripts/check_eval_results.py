from __future__ import annotations

from collections import Counter
from pathlib import Path
import sys

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from integrations.feishu_bitable import FeishuBitableClient  # noqa: E402


def main() -> int:
    load_dotenv(ROOT / ".env")
    client = FeishuBitableClient.from_env()
    if not client.eval_table_id:
        raise RuntimeError("缺少 FEISHU_EVAL_TABLE_ID")
    rows = client.search_records(table_id=client.eval_table_id)
    eval_rows = [
        row
        for row in rows
        if (client._cell_text(row.get("fields", {}).get("用例编号")) or "").startswith("EVAL-")
    ]
    batches = Counter(
        client._cell_text(row.get("fields", {}).get("评测批次")) or ""
        for row in eval_rows
    )
    passed = sum(bool(row.get("fields", {}).get("是否通过")) for row in eval_rows)
    latest_batch = batches.most_common(1)[0][0] if batches else "none"
    print(f"eval_records={len(eval_rows)}")
    print(f"passed_records={passed}")
    print(f"latest_batch={latest_batch}")
    return 0 if eval_rows and passed == len(eval_rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
