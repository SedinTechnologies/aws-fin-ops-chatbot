import traceback
import chainlit as cl
from mcp.types import TextContent, ImageContent

@cl.step(type="tool")
async def call_tool(tool_name, tool_args):
  try:
    resp_items = []
    mcp_tools = cl.user_session.get("mcp_tools", {})
    mcp_name = None

    for c_name, tool_list in mcp_tools.items():
      if any(t.get("name") == tool_name for t in tool_list):
        mcp_name = c_name
        break

    if not mcp_name:
      raise Exception(f"No mcp server registered with tool name: {tool_name}") from None

    mcp_session, _ = cl.context.session.mcp_sessions.get(mcp_name)
    func_response = await mcp_session.call_tool(tool_name, tool_args)

    for item in func_response.content:
      if isinstance(item, TextContent):
        resp_items.append({"type": "text", "text": item.text})
      elif isinstance(item, ImageContent):
        resp_items.append({
          "type": "image_url",
          "image_url": {"url": f"data:{item.mimeType};base64,{item.data}"}
        })
      else:
        raise ValueError(f"Unsupported content type: {type(item)}")

  except Exception as e:
    traceback.print_exc()
    resp_items.append({"type": "text", "text": str(e)})
  return resp_items
