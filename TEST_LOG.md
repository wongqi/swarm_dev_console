# 调试台测试记录

运行说明见 [README](README.md)，板端跨包结果见 [Swarm TEST_LOG](../swarm_mission_coordinator/TEST_LOG.md)。

## 2026-11-24

| 测试 | 覆盖 |
| --- | --- |
| test/test_console.py | 本机 HTTP/MQTT、发布前后 data 一致、显式轮次生成、接收及界面查询不发布控制、凭据脱敏 |
| test/test_reports.py | 证据包路径及摘要校验、只读报告缓存和 ETag 更新 |
| 跨包 ROS 集成 | 调试台发任务后断开 MQTT，模拟设备独立完成，结果和计划正常提交 |
| Firefox | 桌面与窄屏布局、JSON 预览、新轮次编辑、消息筛选、报告与双设备证据回放 |

本轮跨包回归 148 项全部通过，包含 11 项隔离 ROS/MQTT 集成，无跳过；板端六包及 Bringup 构建通过。Firefox 界面检查通过。

测试使用 loopback Broker 和 HTTP。浏览器截图及脚本保存在 runtime/validation/review-20261124。前端布局与文案修改不增加板端主题。

## 复现

源码运行需要系统 Python 和 python3-paho-mqtt。测试另需 python3-pytest 和本机 mosquitto。

```bash
cd ~/colcon_ws/agibot_d1max_ws/src/swarm_dev_console
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q test
```

可用 SWARM_BROKER 指定本机 Mosquitto 可执行文件，未提供可用 Broker 时 HTTP/MQTT 项跳过。报告测试使用模拟只读客户端，真实本机 MinIO 的历史验证由 Swarm TEST_LOG 记录。
