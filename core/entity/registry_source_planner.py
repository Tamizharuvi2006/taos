from __future__ import annotations

from typing import Dict, List


class RegistrySourcePlanner:
    def plan(self, entity_name: str) -> Dict[str, List[str]]:
        entity = str(entity_name or "<entity>").strip() or "<entity>"
        return {
            "official_website": [f"{entity} official website", f"{entity} about company"],
            "registry": [f"{entity} company registry", f"{entity} business registration"],
            "linkedin": [f"{entity} LinkedIn company page"],
            "directory": [f"{entity} business directory", f"{entity} Crunchbase Tracxn"],
            "news": [f"{entity} news interview"],
            "social": [f"{entity} official social profile"],
        }
