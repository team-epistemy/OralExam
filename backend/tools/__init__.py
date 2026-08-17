"""M3 tools (T14/T15): shared handlers surfaced via both REST and MCP."""
from backend.tools.materials_tools import MaterialsTools
from backend.tools.search_tools import SearchTools

__all__ = ["MaterialsTools", "SearchTools"]
