# 安全操作规则

## Git 操作

- 不 force push（`--force` / `-f`）
- 不 reset --hard 他人分支
- 不删除远程分支（除非用户明确要求）
- 不 amend 已 push 的 commit
- commit 前必须 `go build` 通过

## 文件操作

- 不删除不认识的文件（先问用户）
- 不覆盖有未提交修改的文件
- 不修改 `gen_*_optiongen.go`（生成文件）
- 不修改 `wire_gen.go`（除非重新 wire generate）
- 不修改 `.env` / 配置中的密钥

## 验证规则

- 修改代码后必须 `go build -tags actor_id_uint64 ./cmd/server/` 通过
- 不能声称"已修复"但未验证编译
- 不能声称"测试通过"但未运行测试
- 大范围修改后加跑 `go vet`

## 不可逆操作

以下操作执行前必须确认：
- 删除文件/目录
- 清空数据结构
- 修改数据库
- 重置配置
- 发送消息/邮件（游戏内）
