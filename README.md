# Swarm 协同调试台

在开发 PC 临时代替平台发布任务，接收设备心跳、计划和任务事件。设备端六个 Swarm ROS 包自主完成共识、规划和执行。调试台退出后，设备按已提交任务继续运行。

## 安装与启动

使用 Python 3.10 及以上版本，源码可直接通过系统 Python 运行。

```bash
sudo apt install python3-paho-mqtt python3-yaml python3-pil
cd ~/colcon_ws/agibot_d1max_ws/src/swarm_dev_console
python3 -m swarm_dev_console.server --mqtt-host 121.196.36.117 --mqtt-port 20402
```

浏览器打开 http://127.0.0.1:8765。端口通过 --port 配置。MQTT 认证使用 SWARM_MQTT_USER 和 SWARM_MQTT_PASSWORD 环境变量，TLS 使用 --mqtt-tls，私有 CA 通过 --mqtt-ca 指定。

## 任务操作

点击右上角「新建任务」，导入 JSON，核对任务点、设备和地图后进入确认页。任务 ID 与 round_epoch 在进入确认页时，直接使用当前 Unix 毫秒时间戳，例如 `1795513654008`；无需手动输入，不维护轮次计数器。确认页展示实际将发送的请求，点击发送后不再改写内容。

「预览点位」可先在主页面查看任务文件；JSON 编辑收在对话框的折叠区域。追加任务点、停止和重置位于当前任务操作栏，自动关联当前任务的原始任务 ID 与轮次，包括平台创建的任务。追加文件中的轮次在确认前替换为目标任务轮次，其余内容保留。

任务文件包含 robot_ids、map_package、tasks，可保留 round_epoch 样例值；新任务确认时生成本轮值。地图路径必须为设备上真实的绝对目录。参考文件为 [examples/company2_test.json](examples/company2_test.json)，地图本地副本在 maps/company2 和 maps/VY0UgTP，均由 Git 忽略。 [板端任务协议](../swarm_mission_coordinator/README.md#任务输入)

平台与调试台统一采用毫秒时间戳轮次格式；设备表显示各设备当前轮次。两个入口应保持时钟同步并交替下发新任务，避免同时提交。同一轮的追加、停止和重置不生成新轮次。HTTP 发布只调用一次 MQTT publish；设备受理及执行状态通过公开回执与事件确认。

## 分配与重分配

页面按「当前任务 → 分配与重分配 → 设备状态 → 事件、报告与记录」组织。新建任务在对话框内完成，MQTT 原始记录默认折叠。

分配工作区左侧显示地图，右侧显示所选修订的设备任务序列、检查点数及前置设备。每个修订独立保留，可切换轮次、首次分配及各次重分配；相对上一修订标注纳入、移出的点位和顺序调整，移出不等于导航到达。地图同时切换为该修订的检查点及任务归属，支持单独查看某台设备。

默认跟随最新分配。手动选择历史后保持当前视图，新修订到达时显示提示，点击「跟随最新」恢复。分配结果来自现有 for_platform_task_plan 事件，按轮次与修订写入本机 SQLite；重复及乱序补发不会覆盖原记录，刷新或重启调试台后可继续回看。仅展示实际接收到的修订，不向设备追加查询；轮次下拉列出最近 50 轮。

本地保存的是首次接收到的消息，补发消息可能携带当时最新的任务汇总，并非原发生时刻的完整执行快照。右侧展示已提交的路线分配，当前执行状态以顶部任务监控为准。

## 本地地图

按 map_package 的末级目录名匹配本机 maps，例如 `/home/cat/Workspace/Maps/company2` 对应 `maps/company2`。仅用于展示，发送的完整地图路径保持原值。可用 --maps-root 指定其他本机目录；安装版需另行准备地图。

地图 YAML 文件名对应 location.frame_id，坐标换算使用 resolution、origin 及旋转。支持多子地图切换、缩放和平移，点位全程使用原始 ID。大底图在 PC 缩放，坐标仍按原图尺寸计算；地图缺失仅影响预览。虚线表示规划检查点的连接顺序，不是避障路径或实测运动轨迹。地图读取、修订比较和绘制均在本机完成，不增加 MQTT 主题、订阅或设备消息。

## 监控与运行记录

页面展示任务状态、设备心跳、路线计划、事件回执和近期 MQTT 消息。界面标注的「近期收到」表示最近五秒内接收过该设备消息，业务在线结论由板端判断。

调试台订阅固定的平台主题及设备心跳。浏览器刷新、读取记录、导入证据、查看报告及重连 MQTT 都不会发布控制消息。界面通过本机 HTTP 读取已有缓存。设备间共识和图片主题不在调试台订阅范围。

| 数据目录 | 内容 |
| --- | --- |
| runtime/plans.sqlite3 | 按轮次和修订保存的脱敏分配历史 |
| runtime/mqtt | 脱敏后的分段收发日志 |
| runtime/reports | MinIO 报告缓存 |
| runtime/archives | 导入的板端证据和 HTML |

源码默认使用包根的 runtime，安装版本使用 ~/.swarm-dev-console，--data-root 可覆盖。日志由维护者手工归档。「导出接收记录」下载当前缓存快照，完整本地记录保存在 runtime/mqtt。

## 报告与离线回放

查看 MinIO 报告需要安装 python3-boto3，并配置独立的只读账号。Leader 使用任务中提供的写入凭据，调试台使用以下环境变量。

| 变量 | 用途 |
| --- | --- |
| SWARM_REPORT_ENDPOINT | MinIO HTTP 或 HTTPS 地址 |
| SWARM_REPORT_BUCKET | bucket 名称 |
| SWARM_REPORT_PREFIX | 可读取的对象前缀 |
| SWARM_REPORT_ACCESS_KEY | 只读 Access Key |
| SWARM_REPORT_SECRET_KEY | 只读 Secret Key |
| SWARM_REPORT_SESSION_TOKEN | 可选临时凭据 token |

只读账号需要目标前缀下的 s3:GetObject。点击「查看报告」时通过 HTTP 读取 report_key 对应对象，按 ETag 缓存。对象尚未出现可稍后再次打开，无 ListBucket 权限时 MinIO 也可能返回 403。

可导入单个 HTML，或导入同轮多个设备的证据包，合并时间线并按设备筛选。导入和回放使用本地文件。 [板端证据导出](../swarm_mission_coordinator/README.md#运行资料与迁移)

测试范围和复现方式见 [TEST_LOG](TEST_LOG.md)。

## 代码职责

| 模块 | 职责 |
| --- | --- |
| console.py / server.py | 公开 MQTT 接入、本机 HTTP、界面状态 |
| history.py / maps.py / reports.py | 本机分配历史、地图读取、只读报告与证据导入 |
| static/console.js | 当前任务、设备、事件与记录展示 |
| static/task-request.js | 任务草稿、身份生成、确认及发布 |
| static/allocation-view.js / map-view.js | 修订选择与比较、地图叠加及交互 |
