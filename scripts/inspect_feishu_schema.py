from __future__ import annotations

import sys
import json
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from integrations.feishu_bitable import FeishuBitableClient  # noqa: E402


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    load_dotenv(ROOT / ".env")
    client = FeishuBitableClient.from_env()
    for table_name, table_id in (
        ("商机分析表", client.table_id),
        ("阶段规则", client.stage_rules_table_id),
        ("测试用例", client.eval_table_id),
    ):
        print(f"[{table_name}] {table_id}")
        for item in client.list_fields(table_id=table_id):
            print(
                f"- {item.get('field_name')} | type={item.get('type')} | "
                f"primary={item.get('is_primary', False)}"
            )
            if item.get("type") == 3:
                print(
                    "  options="
                    + json.dumps(
                        [option.get("name") for option in item.get("property", {}).get("options", [])],
                        ensure_ascii=False,
                    )
                )
        if table_name == "阶段规则":
            print("记录：")
            for record in client.search_records(table_id=table_id):
                print(json.dumps(record.get("fields", {}), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
