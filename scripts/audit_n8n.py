"""Read schedule metadata only, without credentials or execution payloads."""

import json
import sqlite3

connection = sqlite3.connect("file:/n8n/database.sqlite?mode=ro", uri=True)
for name, active, nodes in connection.execute("SELECT name, active, nodes FROM workflow_entity"):
    print(
        json.dumps(
            {
                "name": name,
                "active": bool(active),
                "schedules": [
                    node.get("parameters", {}).get("rule")
                    for node in json.loads(nodes)
                    if "scheduleTrigger" in node.get("type", "")
                ],
            },
            ensure_ascii=False,
        )
    )
connection.close()
