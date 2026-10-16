# 好友圈自有部署服务端

用 Flask 重写的 `com.xtc.moment` 后端，供自用测试。当前版本**只接受不加密的请求**：
手表把身份信息放在明文 `Base-Request-Param` 请求头里，服务端直接读这个头完成鉴权，
不涉及任何加解密逻辑。账号可以在自带的 dashboard 里添加和管理。

## 快速开始

```powershell
cd D:\moment-server
python -m pip install -r requirements.txt
python run.py
```

默认监听 `0.0.0.0:8000`，第一次启动会自动建库并创建一个管理员账号：

| 用户名 | 密码 | watchId |
|---|---|---|
| `admin` | `admin123` | `admin` |

登录入口：`http://<服务器IP>:8000/login`。登录后即可新增/编辑/停用/删除手表账号，
查看每个账号的动态、评论、点赞数量，撤销 token，以及删除动态。

> 首次启动后请立刻修改默认密码。也可以用环境变量覆盖：
> `MOMENT_ADMIN_USERNAME` / `MOMENT_ADMIN_PASSWORD` / `MOMENT_ADMIN_WATCH_ID` /
> `MOMENT_SECRET_KEY` / `MOMENT_DATABASE_URL` / `MOMENT_MEDIA_DIR` /
> `MOMENT_HOST` / `MOMENT_PORT`。

## 鉴权方式

手表端的 `HttpHelper.buildBaseRequestParamJson` 会把下列字段拼成 JSON 放进
`Base-Request-Param` 头：

```json
{
  "accountId": "<watchId>",
  "appId": "2",
  "deviceId": "...",
  "imFlag": "1",
  "mac": "...",
  "program": "watch",
  "registId": 1,
  "requestId": "...",
  "timestamp": "2026-10-16 12:00:00",
  "token": "<chipId>"
}
```

注意 `token` 字段里装的**不是**会话凭证，而是硬件芯片号
`ro.boot.xtc.chipid`（见 `WatchDevice.getChipId()`，读不到时返回字面量 `unkown`）。
它和 `mac`、`registId`、`deviceId` 一样属于设备标识；原厂真正用来验账号的是
`accountId` 加上 `Eebbk-Sign` 签名，签名密钥来自服务端派发的 aesKey。

服务端用 `accountId` 去 `users.watch_id` 里找账号：

* 找不到或 token 失效 → `000003`
* 账号被停用 → `000004`
* 账号绑定了 chipId 但请求来自别的设备 → `000016`（与客户端
  `HttpAuthManager.AUTH_ERROR_CODE` 一致，会触发重新鉴权）
* 其余情况正常放行

### chipId 设备绑定

`users.chip_id` 为空表示"不限制设备"，此时任何知道 watchId 的手表都能用；
服务端会顺手把最近一次见到的 chipId 记到 `users.last_chip_id`。
在 dashboard 里：

* 表格里 `未绑定（最近 xxx）` 时点「绑定该设备」，账号就锁定到那块表
* 已绑定时点「解绑设备」恢复不限制
* 新增/编辑账号也可以直接填 chipId；同一个 chipId 不能绑定给两个账号

`unkown`（手表读不到芯片号时的兜底值）永远不会被记录成 `last_chip_id`，
也不会在"未绑定"状态下参与校验 —— 否则所有读不到芯片号的手表会互相顶掉。
但账号一旦绑定，`unkown` 同样通不过校验，等于该表暂时不可用；这时在 dashboard
点「解绑设备」即可恢复。

响应统一是 `{"code": "000001", "data": ..., "desc": "", "watchTips": ""}`，
只有 `000001` 会被客户端的 `HttpRxJavaCallback` 当作成功。

另外提供一套可选的可撤销 token：

* `POST /moment/auth/login`，body `{"username": "...", "password": "..."}`，
  可选再传 `chipId` 做设备校验；返回 `data.token`
* 之后请求带 `authId: <token>` 头即可，不再需要 `Base-Request-Param`
* `POST /moment/auth/logout` 撤销该 token

## 已实现的接口

与 `com.xtc.moment.net.MomentHttp` 一一对应：

`/moment/public`、`/moment/delete`、`/moment/search`、`/moment/refreshos`、
`/moment/isBannedToPost`、`/moment/like`、`/moment/like/cancel`、`/moment/like/search`、
`/moment/comment`、`/moment/deleteComment`、`/moment/getMomentComment`、
`/moment/getMomentCommentAll`、`/moment/permission/search`、`/moment/permission/update`、
`/moment/doLbsStar`、`/moment/template`、`/moment/vlog/gift/search`、
`/moment/vlog/gift/sendgift`、`/moment/advertComment/*`、
`GET /social-service/warmReminder/getWarmReminderConfig`、
`GET /moment-service/topic/recommend/list`、`POST /social-service/inform/*`。

### 图片上传（暂不支持）

`/moment/file/pic/transfer`、`/moment/file/pics/transfer`、
`/moment/file/video/transfer` 现在会明确返回失败。

原因：手表拿到 token 后是交给七牛 SDK 直接上传到七牛自己的域名，
自有服务器没法用返回值把上传地址改过来。要让图片上传走本机，需要改客户端
（比如替换 `MomentPhotoServeImpl.uploadPhoto` 的上传实现），这一步按你的要求没有动。

文字动态、点赞、评论、话题这些不依赖云存储的路径已经完整可用。

## 让手表指向这个服务器

在 App 侧把好友圈服务域名指到本机（只改配置，不动业务逻辑）：

* `com.xtc.domain.Domain.MOMENT` 的 CN/SA 域名改成 `<服务器IP>:8000`
* `com.xtc.httplib.net.BaseUrlManager.PROTOCOL_TYPE` 改成 `ConfigOptions.ProtocolType.HTTP`
  （当前服务器只跑明文 HTTP）
* 若还用到 `/social-service/*`，把 `Domain.GATEWAY` 一并指过来

`/moment-service/topic/recommend/list` 走的是网关地址，同样需要指向本机。

## 测试

```powershell
python -m unittest discover -s tests -v
```

用例覆盖：管理员登录、新增账号、明文头鉴权、发动态、拉列表、点赞、评论、
话题回显、停用账号被拒、token 登录。

## 目录结构

```
app/
  __init__.py      应用工厂：建库、建管理员、注册蓝图、/media 路由
  config.py        配置（可用环境变量覆盖）
  models.py        SQLAlchemy 模型：users / auth_tokens / moments / comments / likes
  api_common.py    响应信封、明文头解析、鉴权装饰器
  api.py           好友圈接口
  auth.py          登录 / 登出 / dashboard 会话
  dashboard.py     管理员后台
  serializers.py   模型 → 客户端 JSON
  templates/       base / login / dashboard / moments
  static/          style.css
run.py             启动入口
tests/test_server.py
```
