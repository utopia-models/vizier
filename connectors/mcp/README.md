# Vizier MCP server

`vizier_mcp.py` lets any MCP client drive a Vizier server. Standard library only (Python 3.9+), stdio transport.

```bash
claude mcp add vizier -e VIZIER_URL=http://127.0.0.1:8786 -- python3 /path/to/connectors/mcp/vizier_mcp.py
```

Or in any client's JSON config:

```json
{ "mcpServers": { "vizier": { "command": "python3", "args": ["/path/to/connectors/mcp/vizier_mcp.py"],
                              "env": { "VIZIER_URL": "http://127.0.0.1:8786" } } } }
```

| tool | does |
|---|---|
| `post_bullets {bullets, for?}` | shows 1-6 bullets of what to say next |
| `post_line {ch, text, final?}` | shows a transcript line (`me` / `them`) |
| `read_transcript {since?}` | the open call's final lines, `[seq] ME/THEM: text` |
| `set_script {markdown}` | puts a script on the teleprompter |
| `set_status {text}` · `set_note {text}` | the status line · the prompter's one-line note |
| `start_call {call_id?, title?}` · `end_call` | open / close a call |

| env | meaning |
|---|---|
| `VIZIER_URL` | the server (default `http://127.0.0.1:8786`) |
| `VIZIER_TOKEN` | bearer token, if the server has `VIZIER_TOKEN` set |
| `VIZIER_MCP_PUSH=1` | also push each new final line as a Claude Code channel event (`notifications/claude/channel`, content `ME: …` / `THEM: …`, meta `ch`, `seq`, `call`). Start Claude Code with the channel enabled to receive them; other hosts ignore it. |
