# Swarm 协同调试台

在开发 PC 临时代替平台发布任务，接收设备心跳、计划和任务事件。设备端六个 Swarm ROS 包自主完成共识、规划和执行。调试台退出后，设备按已提交任务继续运行。

## 安装与启动

使用 Python 3.10 及以上版本，源码可直接通过系统 Python 运行。

```bash
sudo apt install python3-paho-mqtt
cd ~/colcon_ws/agibot_d1max_ws/src/swarm_dev_console
python3 -m swarm_dev_console.server --mqtt-host 121.196.36.117 --mqtt-port 20402
```

浏览器打开 http://127.0.0.1:8765。端口通过 --port 配置。MQTT 认证使用 SWARM_MQTT_USER 和 SWARM_MQTT_PASSWORD 环境变量，TLS 使用 --mqtt-tls，私有 CA 通过 --mqtt-ca 指定。

## 发布任务

1. 选择操作并填写任务 ID，导入任务文件或粘贴任务 JSON。
2. 核对 round_epoch、设备名单、地图绝对路径和任务点。新轮次需要递增值，可点击「生成新轮次」显式更新编辑区。
3. 预览请求并发送。发送端保留编辑区中的 data 内容，只添加协议外层的 tid、timestamp 和任务 ID。

新任务和追加任务的 round_epoch 都来自 JSON。停止和重置使用界面填写的当前轮次。HTTP 发布请求只发送一次，设备受理、共识提交及最终执行状态由接收到的 MQTT 消息反馈。

室内参考任务位于 [examples/company2_test3.json](examples/company2_test3.json)。按实际在线编队填写 robot_ids，各参与设备须能读取 map_package 指向的绝对目录。样例包含七个原始点位和 round_epoch 的演示值，首次实测前请生成本轮的新值。

地图本地副本在 maps/company2 和 maps/VY0UgTP，由 Git 忽略。地图资源预先部署到设备，调试台只发送任务 JSON。 [板端任务协议](../swarm_mission_coordinator/README.md#任务输入)

## 监控与运行记录

页面展示任务状态、设备心跳、路线计划、事件回执和近期 MQTT 消息。界面标注的「近期收到」表示最近五秒内接收过该设备消息，业务在线结论由板端判断。

调试台订阅固定的平台主题及设备心跳。浏览器刷新、读取记录、导入证据、查看报告及重连 MQTT 都不会发布控制消息。界面通过本机 HTTP 读取已有缓存。设备间共识和图片主题不在调试台订阅范围。

| 数据目录 | 内容 |
| --- | --- |
| runtime/epoch | 显式生成的新轮次计数 |
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

只读账号需要目标前缀下的 s3:GetObject。点击「查看任务报告」时通过 HTTP 读取 report_key 对应对象，按 ETag 缓存。对象尚未出现可稍后再次打开，无 ListBucket 权限时 MinIO 也可能返回 403。

可导入单个 HTML，或导入同轮多个设备的证据包，合并时间线并按设备筛选。导入和回放使用本地文件。 [板端证据导出](../swarm_mission_coordinator/README.md#运行资料与迁移)

测试范围和复现方式见 [TEST_LOG](TEST_LOG.md)。
