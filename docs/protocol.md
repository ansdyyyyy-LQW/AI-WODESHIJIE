# Bridge 协议 v1

传输地址默认 `ws://127.0.0.1:8765`。Forge 是 WebSocket Client，Agent Core 是 Server。

所有消息使用统一包络：

```json
{
  "protocol_version": 1,
  "type": "ACTION_REQUEST",
  "session_id": "...",
  "message_id": "uuid",
  "maid_uuid": "uuid-or-null",
  "game_tick": 123,
  "timestamp_ms": 0,
  "payload": {}
}
```

必需消息：`HELLO`、`PING/PONG`、`STATE_SNAPSHOT`、`STATE_RESYNC`、`EVENT`、`ACTION_REQUEST`、`ACTION_ACK`、`ACTION_RESULT`、`DISCOVER_MAIDS`、`MAID_LIST`、`BIND_MAID`、`UNBIND_MAID`、`SAFE_IDLE`。

`ACTION_ACK` 只表示接受；最终结果以 `ACTION_RESULT` 为准。重复 `request_id` 不应执行两次。网络回调只入队，世界修改只在服务器 Tick 线程执行。
