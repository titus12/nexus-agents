# RTC SDK Compat 登录 Singleflight 需求

## 1. 背景

当前业务层在 `RTMManager.Login()` 中会先关闭已有客户端，再创建新的 `RTMClient` 并执行登录。断线重登由业务层定时调用 `ChatModule.LoginRtm()` 实现。

这种方式存在以下问题：

- 并发登录可能创建多个 Runtime；
- 重登过程中可能关闭仍在工作的 Runtime；
- Token 更新、登录、重连和关闭的并发关系由业务层维护，容易产生竞态；
- Compat 内部已有 Runtime 管理能力，但业务层没有统一复用入口。

本需求要求将登录去重和 Runtime 复用能力下沉到 RTC SDK Compat，业务层只负责提交登录信息。

## 2. 目标

Compat 的登录接口需要具备幂等和 singleflight 能力：

1. 同一个业务会话同时只能存在一个有效 Runtime；
2. 同一个业务会话的并发 Login 请求只能产生一次底层登录流程；
3. 已有 Runtime 正在工作时，不关闭 Runtime、不重复创建 Runtime；
4. 新 Login 传入的 Token 更新为该会话的最新 Token；
5. 没有可复用 Runtime 时，才创建 Runtime 并执行正常 Login；
6. 登录、重连、关闭之间不能互相覆盖或回写过期状态。

## 3. 会话标识

同一会话默认由以下身份确定：

```text
projectId + uid
```

同一 `projectId + uid` 应复用同一个 Compat Runtime 和同一个登录流程。

Endpoint 是 Runtime 的连接属性，不是 Token 属性：

- Endpoint 相同：允许复用 Runtime；
- Endpoint 仅 Token 不同：只更新 Token；
- Endpoint 发生变化：不能仅更新 Token，需要按新 Endpoint 建立新的连接流程；
- 不同 `projectId` 或不同 `uid`：必须使用相互独立的 Runtime 和登录流程。

## 4. Runtime 状态语义

以下状态视为“已有 Runtime 正在工作”，Login 不得关闭或重新创建 Runtime：

- 登录请求进行中；
- `Connecting`；
- `Connected`；
- `Reconnecting`。

以下状态视为不可复用，需要进入新的登录流程：

- `Closed`；
- `ReconnectFailed`；
- 已释放或已进入终止关闭流程的 Runtime。

`ConnectionIsAlive()` 只能表示当前是否 `Connected`，不能作为 singleflight 的唯一判断条件。singleflight 必须同时判断登录中的 Runtime 和重连中的 Runtime。

## 5. Login 行为要求

### 5.1 没有可用 Runtime

当会话没有 Runtime，或者已有 Runtime 已处于终止状态时：

1. 创建一个新的 Runtime；
2. 保存本次 Endpoint、Token、时间戳和其他认证参数；
3. 发起一次底层 Login；
4. 将登录结果通知所有等待该登录流程的调用方。

### 5.2 Runtime 正在工作

当会话已有正在工作的 Runtime 时：

1. 不调用 `Close()`；
2. 不创建新的 `RTMClient`；
3. 更新会话保存的最新 Token；
4. 不额外发起第二次底层 Login；
5. 后续重连或重新认证必须使用最新 Token。

对于不同状态，调用方完成通知规则如下：

- `Connected`：Token 更新完成后，将当前已连接会话视为复用成功，当前调用方收到一次成功完成通知；
- `Connecting`：加入当前登录流程，等待当前流程完成后收到结果；
- `Reconnecting`：加入当前恢复流程，等待恢复成功或失败后收到结果。

如果 Runtime 已经 `Connected`，更新 Token 本身不应强制触发立即重新认证；除非未来另行定义“Token 刷新并立即生效”的需求。

### 5.3 并发 Login

同一会话的多个并发 Login 请求必须合并：

```text
Login A ─┐
Login B ─┼─> 一次底层 Login
Login C ─┘
```

要求：

- 只能创建一个 Runtime；
- 只能发出一次底层认证请求；
- 每个调用方的完成回调最多执行一次；
- 所有调用方收到同一次登录流程的最终结果；
- 不同会话之间不能互相阻塞或合并。

## 6. Token 更新规则

采用“最新 Token 优先”策略：

- Runtime 已连接：仅更新保存的 Token，不主动重新登录；
- Login 尚未真正发出认证请求：本次登录使用最新 Token；
- 认证请求已经发出：本次请求继续使用已发出的 Token，后续重连必须使用最新 Token；
- 当前登录失败且存在更新后的 Token：允许下一次恢复流程使用最新 Token；
- Token 更新不能导致旧 Runtime 被主动关闭。

Token 不能因为并发 Login 被旧请求覆盖。后到达的有效 Token 必须成为会话保存的最新 Token。

## 7. 关闭和登录的并发要求

Compat 必须保证 Login 与 Close/Bye 的顺序一致性：

- Close 已经开始后，不能再让旧 Runtime 接收新的登录回调；
- Login 不得在旧 Runtime 关闭完成前创建第二个并行 Runtime；
- 旧 Runtime 的延迟回调不能修改新 Runtime 的状态；
- 终止关闭完成后，下一次 Login 必须可以创建新 Runtime；
- `SessionClosed`、登录完成和状态变更事件不能重复通知同一生命周期结果。

## 8. 业务层要求

业务层只负责：

- 提交 `projectId`、`uid`、Endpoint、Token、时间戳等登录参数；
- 处理登录成功、失败和状态变化；
- 根据业务需要决定是否继续请求登录。

业务层不再负责：

- 通过 `Close() + new RTMClient()` 实现登录去重；
- 判断是否已有 Runtime 正在 Login、Connecting 或 Reconnecting；
- 维护并发 Login 的等待队列；
- 直接操作 Compat Runtime 的 singleflight 锁或内部状态；
- 使用 `CheckRelogin` 实现登录并发控制。

现有业务层的断线重登策略是否继续保留，与本需求中的 Login singleflight 是两个独立问题。即使业务层仍然定时调用 `LoginRtm()`，重复调用也不能导致重复 Runtime 或重复底层登录。

## 9. 自动重连边界

本需求只要求 Compat 实现 Login 的 singleflight 和 Runtime 复用，不要求本阶段改变现有 `autoRelogin` 开关语义。

也就是说：

- `autoRelogin` 是否开启需要单独评估和验收；
- Login singleflight 必须在 `autoRelogin = false` 时也成立；
- 业务层重复触发 Login 时，Compat 仍必须完成去重；
- Compat 内部 `TryReconnect()` 的并发去重不能替代 Login singleflight。

## 10. Endpoint 变化要求

如果新 Login 的 Endpoint 与当前 Runtime 不一致：

- 不能只更新 Token 后继续使用旧连接；
- 新 Endpoint 必须成为新的连接目标；
- 旧 Runtime 的回调不能污染新 Runtime；
- 同一时间仍只能有一个对外生效的 Runtime；
- 新 Endpoint 登录成功后，旧 Runtime 必须完成注销和资源释放。

## 11. 验收标准

### 单次登录

- 首次 Login 只创建一个 Runtime；
- 只产生一次底层认证请求；
- 登录成功回调只执行一次。

### 并发登录

- 同一会话并发调用多次 Login，只创建一个 Runtime；
- 只产生一次底层认证请求；
- 所有调用方都收到结果，且每个回调只执行一次。

### 已连接时更新 Token

- 已连接 Runtime 不被关闭；
- 不创建新 Runtime；
- 不额外发起 Login；
- 后续重连使用新 Token。

### 登录过程中更新 Token

- 不创建第二个 Runtime；
- 不发起第二次并行 Login；
- 认证尚未发出时使用新 Token；
- 认证已发出时，下一次认证使用新 Token。

### 断线重登

- 业务层重复调用 `LoginRtm()` 不产生多个 Runtime；
- 旧 Runtime 进入终止状态后才允许创建新 Runtime；
- 新 Runtime 的状态不会被旧 Runtime 的延迟回调覆盖。

### Endpoint 切换

- Endpoint 变化能够建立新连接；
- 旧 Runtime 不再接收有效业务回调；
- 最终只有新 Endpoint 对应的 Runtime 对外生效。

### 关闭竞态

- Close 与 Login 并发时不会出现双 Runtime；
- 不会出现旧 Login 回调修改新 Runtime 状态；
- 不会重复触发 `SessionClosed`。

## 12. 非目标

本需求暂不包含：

- 修改服务端协议；
- 修改 Token 的生成和刷新接口；
- 重新设计聊天业务状态机；
- 开启或关闭 Compat 自动重连策略；
- 修改 `HybridCLRData/`；
- 将 `ConnectionIsAlive()` 改造成登录并发控制接口。
