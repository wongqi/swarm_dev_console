# 调试台测试记录

运行说明见 [README](README.md)，板端跨包结果见 [Swarm TEST_LOG](../swarm_mission_coordinator/TEST_LOG.md)。

## 2026-11-24

| 测试 | 覆盖 |
| --- | --- |
| test/test_console.py | 本机 HTTP/MQTT、发布前后 data 一致、直接采集毫秒轮次、接收及界面查询不发布控制、凭据脱敏 |
| test/test_reports.py | 证据包路径及摘要校验、只读报告缓存和 ETag 更新 |
| test/test_history.py | 重复及乱序修订、不可变首条记录、轮次隔离、凭据脱敏、文件权限与重启恢复 |
| test/test_maps.py | 本机地图目录匹配、多子地图元数据、PGM 转 PNG、原点旋转、非法元数据及越界路径拒绝 |
| 跨包 ROS 集成 | 调试台发任务后断开 MQTT，模拟设备独立完成，结果和计划正常提交 |
| Firefox | 桌面与窄屏布局、JSON 预览、毫秒任务身份确认、消息筛选、报告与双设备证据回放 |

本次调试台 16 项测试全部通过（0.92 秒），无跳过。固定系统时钟验证轮次直接等于 Unix 毫秒时间戳，收到更高轮次也不修改生成值；发送 data 与确认内容一致。

另运行 2 项隔离 ROS/MQTT 集成（48.05 秒）：Leader 掉线接管后调试台下发新轮次，以及调试台断开后设备独立完成任务。板端代码未修改。

Firefox 验证任务文件导入、非法 JSON 提示、毫秒身份确认、重复点击防重发；连续三次分配、点位纳入/移出、历史视图固定、最新跟随、单设备地图过滤；乱序补发、430 条消息挤出近期缓存后回看、重启恢复、移动端布局。平台创建的任务保留原 bid，用于追加、停止和重置。全程只有 4 条用户确认的控制发布，其余读取与绘制均不产生 MQTT 消息。日志、JUnit、脚本与截图位于 runtime/validation/workbench-20261124。

同日既有验证保留：七包及 Bringup 的 148 项跨包回归记录见协调器 TEST_LOG；室内七点、室外七张子地图、坐标旋转及缩放验证见 runtime/validation/map-preview-20261124；报告预览与双设备证据回放见 runtime/validation/review-20261124。以上测试使用本机 Broker、模拟位姿与导航，未连接开发板或生产服务。

## 复现

源码运行需要系统 Python、python3-paho-mqtt、python3-yaml 和 python3-pil。测试另需 python3-pytest 和本机 mosquitto。

```bash
cd ~/colcon_ws/agibot_d1max_ws/src/swarm_dev_console
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q test
```

可用 SWARM_BROKER 指定本机 Mosquitto 可执行文件，未提供可用 Broker 时 HTTP/MQTT 项跳过。报告测试使用模拟只读客户端，真实本机 MinIO 的历史验证由 Swarm TEST_LOG 记录。
