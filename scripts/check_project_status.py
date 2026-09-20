"""Print public Karlancer project status for one or more identifiers."""

import json
import sys
import urllib.request

for project_id in sys.argv[1:]:
    with urllib.request.urlopen(
        f"https://www.karlancer.com/api/publics/projects/{project_id}", timeout=30
    ) as response:
        payload = json.load(response)
    data = payload.get("data") or {}
    print(project_id, data.get("status"), data.get("title"))
