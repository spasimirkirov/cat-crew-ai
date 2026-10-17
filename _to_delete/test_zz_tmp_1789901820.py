from backend.db import ledger as db
from backend.platform import role_tools
from backend.platform.tool_registry import register_agent_tools
from backend.tools import execute_tool
def test_x(isolated):
    db.init_db(); role_tools.ensure_role_tools()
    print("REG", register_agent_tools("pulse"))
    print("RES", execute_tool("git_diff_inspector", {})[:200])
