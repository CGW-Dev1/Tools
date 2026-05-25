# Outlook/Hotmail 最新邮件批量获取工具

这是一个本机桌面工具，用 Microsoft Graph 或 IMAP OAuth 读取已授权 Outlook/Hotmail 账号的最新收件箱邮件。

## 安全模型

- 导入账号时保存邮箱、密码、client_id、refresh_token 四段内容。
- 四段内容使用 Windows DPAPI 加密后保存在当前 Windows 用户的 AppData 目录。
- 也保留 Microsoft Graph/MSAL 交互授权作为备用方式。

## 准备 Microsoft Graph 应用

1. 在 Microsoft Entra 管理中心创建应用注册。
2. 支持的账号类型选择个人 Microsoft 账号，或个人账号 + 组织账号。
3. 添加公共客户端/移动和桌面平台重定向 URI：`http://localhost`。
4. 添加 delegated permission：`Mail.Read`。
5. 复制 Application (client) ID 到本工具。

## 运行

```powershell
python -m pip install -r requirements.txt
python app.py
```

## 打包 exe

```powershell
.\build.ps1
```

打包完成后 exe 位于：

```text
dist\邮件验证码助手.exe
```

## 官方参考

- Microsoft Graph list messages: https://learn.microsoft.com/en-us/graph/api/user-list-messages
- MSAL Python acquire tokens: https://learn.microsoft.com/en-us/entra/msal/python/getting-started/acquiring-tokens
- Microsoft Graph auth concepts: https://learn.microsoft.com/en-us/graph/auth/auth-concepts
- Microsoft identity refresh token flow: https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-auth-code-flow
- IMAP/POP/SMTP OAuth: https://learn.microsoft.com/en-us/exchange/client-developer/legacy-protocols/how-to-authenticate-an-imap-pop-smtp-application-by-using-oauth

## 导入格式

工具兼容类似下面的行格式：

```text
email@outlook.com----password----client-or-tenant-id----token
```

程序会读取并本地加密保存：

- 第 1 段：邮箱
- 第 2 段：密码
- 第 3 段：client_id
- 第 4 段：Graph refresh_token

导入后如果勾选“导入后自动取件”，程序会立即按当前协议设置批量获取邮件。默认协议是 Graph。

## 功能

- 批量导入账号
- 导入后自动取件
- Graph / IMAP / Graph优先 / IMAP优先
- 5、10、20、30 封快捷数量
- 获取选中账号或全部账号
- 查看邮件主题、发件人、预览内容
- 导出 CSV
- 删除、清空账号和结果
