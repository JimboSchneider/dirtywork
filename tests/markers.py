# Built by concatenation ON PURPOSE: a local worker model editing tests through its tool
# channel cannot emit its own chat template's control tags literally (see the comment in
# dirtywork/toolspec.py next to TOOL_CALL_MARKERS).
TOOL_CALLS = "[" + "TOOL_CALLS]"          # Devstral / Mistral
TOOL_CALL_OPEN = "<" + "tool_call>"        # Qwen-style XML, opening tag
TOOL_CALL_CLOSE = "</" + "tool_call>"      # Qwen-style XML, closing tag
