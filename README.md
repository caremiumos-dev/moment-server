# 好友圈自有部署服务端

用 Flask 重写的 `com.xtc.moment` 后端，可用于小规模自建部署。当前版本**只接受不加密的请求**：
手表把身份信息放在明文 `Base-Request-Param` 请求头里，服务端直接读这个头完成鉴权，
不涉及任何加解密逻辑。账号可以在自带的 dashboard 里添加和管理。

## Docker 部署（推荐）

```bash
cp .env.example .env
# 编辑 .env：至少改 MOMENT_SECRET_KEY 和 MOMENT_ADMIN_PASSWORD
docker compose up -d --build
docker compose ps          # 等到 STATUS 显示 healthy
```

服务监听宿主机 `8000` 端口，数据（SQLite 库 + 上传的图片/视频）保存在
`moment-data` 命名卷里，容器重建不会丢。日志直接进 stdout：

```bash
docker compose logs -f --tail 100
```

镜像基于 `python:3.12-slim`，用 gunicorn（2 worker × 4 thread）跑，
以非 root 用户 `moment` 运行，带 `/healthz`、`/readyz` 健康检查。
`MOMENT_SEED_DEFAULTS=true` 时首次启动会写入默认心情/状态模板和推荐话题。

构建阶段的 APT/PyPI 源可以在 `.env` 里换（默认阿里云）：

```dotenv
APT_MIRROR=mirrors.aliyun.com
PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
```

`APT_MIRROR` 也接受完整地址（如 `https://mirrors.tuna.tsinghua.edu.cn`）；
`PIP_INDEX_URL` 留空则使用 pypi.org。改完重新 `docker compose build`。

### 生产环境注意事项

* **必须**改掉 `MOMENT_SECRET_KEY`。仍为默认值时，生产入口会直接拒绝启动。
* 用 `docker compose up -d --build` 部署后，`admin` 的密码只用于建库那一次，
  请先在 `.env` 改好再启动。
* 直接暴露在公网时，建议前面加一层 HTTPS 反代（nginx/Caddy），并设置
  `MOMENT_SECURE_COOKIES=true`、`MOMENT_PROXY_COUNT=1`、`MOMENT_PUBLIC_BASE_URL`。
* 服务端目前只支持明文请求；如果客户端开了加密，本服务无法解析。
* dashboard 的所有写操作都有 CSRF 校验，会话 cookie 为 HttpOnly + SameSite=Lax。
* 备份：

  ```bash
  ./scripts/backup.sh /path/to/backups
  ```

  会生成包含数据库快照和 media 的 tar.gz；用的是 sqlite `.backup`，
  服务运行中也能得到一致快照。

### 常用运维命令

```bash
docker compose restart          # 重启
docker compose pull && docker compose up -d --build   # 升级
docker compose exec moment python seed.py            # 手动补默认模板/话题
docker compose exec -T moment python -c "import sqlite3; ..."  # 直接查库
```

## 本地开发

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

## 封禁账号

dashboard 的账号列表里点「封禁」即可。封禁后的行为：

* **所有接口**都返回线上那条封禁响应（`app/api_common.py` 的 `fail_banned`）：

  ```json
  {"code":"000007","data":null,"desc":"request parameter is invalid.","watchTips":"Your account has been suspend"}
  ```

* **无法登录**：`/moment/auth/login` 同样返回上面的包体
* 已签发的 token 会一并撤销，手表下次请求立刻感知
* 列表里该行显示 `已封禁`，按钮变成「解除封禁」

客户端的 `PublishErrorUtil` 会把 `000007` 识别成账号封禁，弹出
**「你的账户已被封禁」** 弹窗，正文为
「经过我们的内部调查，您可能存在违反我们的用户协议的行为，如果你觉得这是一个错误，请点击申请复核，否则您可以下载您所发布过的动态」，
三个按钮分别是 **申请复核** / **下载我发布过的动态** / **我知道了**。
拉取动态列表失败时也会弹这个窗，不只是发布失败。

### 申请复核

「申请复核」调用 `POST /extra/review-request`（body：`watchId` / `reason` / `contact`）。
这个接口对封禁账号**放行** —— 否则它会先被 `000007` 拦掉，申诉入口就没意义了，
所以它的装饰器是 `@authenticate(required=False, allow_banned=True)`。

提交后 dashboard 的「复核申请」卡片会列出来，可以「标记已处理」。

### 下载已发布的动态

「下载我发布过的动态」打开好友圈主页面。原厂没有批量导出接口，
所以这里保持同样的行为，不做额外的打包下载。

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

### 心情/状态模板

发布页进入时会调用 `POST /moment/template`，把返回的 `resources` 全部写进
本地 `moment_template` 表，之后心情列表（`type=0`）和状态列表（`type=1`）
都从这张表读。所以后台 `/templates` 页就是这两个列表的数据源：

* 每条模板必须有 `content` 和 `resource`，否则发布页会直接退出。
* `resourceId` 是客户端 upsert 的主键，由后台自动递增，不要手工改重。
* `resource` 支持填完整 url；从后台直接上传图标时存成 `media:<文件名>`，
  下发时自动展开成 `<服务器地址>/media/<文件名>`。
* 停用一条模板后它不再出现在下一次的 `resources` 里。

`updateId` 按"增量拉取"语义实现：带 0 拉全量，带上次返回的 `updateId`
只回传之后更新的行。

### 小括号（话题）

`GET /moment-service/topic/recommend/list` 返回两部分：

* `config.maxTopicCount`：一条动态最多挂几个话题，客户端用它做数量校验；
  `topicNameMaxLength` 是名称长度上限，颜色用于渲染。
* `topicList`：后台 `/topics` 页配置的官方话题（`topicType=0`），外加
  用户自己发过的话题（`topicType=1`），同名时官方配置优先。

两者都在后台管理，改动立刻生效，不需要重启。

### 图片上传

`/moment/file/pic/transfer`、`/moment/file/pics/transfer`、
`/moment/file/video/transfer` 会签发指向本机 `/moment/file/upload` 的 token，
文件落在 `MEDIA_DIR`，通过 `/media/<name>` 读回。客户端侧由
`com.xtc.qiniu.SelfHostedUploader` 接管上传。

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
  models.py        SQLAlchemy 模型：users / auth_tokens / moments / comments /
                   likes / moment_templates / topics / settings
  api_common.py    响应信封、明文头解析、鉴权装饰器
  api.py           好友圈接口
  auth.py          登录 / 登出 / dashboard 会话
  dashboard.py     管理员后台
  serializers.py   模型 → 客户端 JSON
  templates/       base / login / dashboard / moments / templates / topics
  static/          style.css
run.py             启动入口
seed.py            写入默认心情/状态模板与推荐话题
wsgi.py            gunicorn 生产入口
Dockerfile / docker-compose.yml
docker-entrypoint.sh
assets/fonts/      Noto Color Emoji（OFL-1.1），生成默认图标用
scripts/backup.sh  数据库 + media 备份
tests/test_server.py
```
