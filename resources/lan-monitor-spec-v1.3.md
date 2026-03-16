# LAN Monitor — 局域网拓扑监控系统 Spec

**Version: v1.3**

---

# 1. 项目目标

开发一个基于 **Python + Flask** 的局域网拓扑监控软件，用于持续监控局域网内部主机之间的网络状态，并通过 Web 页面实时展示网络拓扑、链路延迟、异常状态、刷新模式以及当前服务连接状态。系统核心由后端探测线程、状态缓存、拓扑发布线程和前端实时渲染组成。

系统特点：

- 程序后台持续运行
- 用户无需一直打开浏览器
- 用户访问 Web 页面即可查看当前拓扑状态
- 页面实时展示：
  - 节点在线状态
  - 链路延迟
  - 网络异常
  - 当前连接状态
  - 当前刷新模式
  - 最后更新时间
- 支持多个客户端同时访问
- 支持通过配置控制服务监听地址与端口
- 支持日志目录权限检测、自动回退与运行时禁用日志
- 支持 Linux x86 与 ARM 环境部署

---

# 2. 技术栈

## 2.1 后端

- Python 3.8
- Flask 2.0.2
- Flask-SocketIO 5.3.2
- eventlet 0.33.3

## 2.2 前端

- socket.io-client
- cytoscape.js
- cytoscape-cose-bilkent

## 2.3 运行环境

支持：

- Linux
- x86 架构
- ARM 架构

说明：

- 旧版本 Spec 以 Ubuntu 20.04 Desktop / x86 为基准
- 当前版本已经面向 Linux ARM 环境部署场景进行适配设计，例如服务监听地址配置化、日志目录权限容错等

---

# 3. 系统架构

系统由四部分组成：

```text
Ping Worker
    ↓
状态缓存 latest_results
    ↓
Publisher
    ↓
SocketIO
    ↓
Web 前端
```

## 3.1 Worker

负责：

- 定期 ping 设备
- 写入延迟结果缓存

特点：

- 每个 IP 一个 worker
- worker 独立运行
- 互不阻塞
- 某个设备探测异常不会阻塞其他设备
- 每个 worker 周期根据当前模式动态切换为 normal 或 fast 频率

## 3.2 状态缓存

缓存结构：

```text
latest_results
```

每个设备缓存内容包括：

- latency
- status
- updated_at
- fail_count

作用：

- 解耦 worker 与 publisher
- 保存每个目标设备的最后逻辑状态
- 提供离线防抖与状态迟滞判断的基础数据

## 3.3 Publisher

负责：

- 从缓存生成拓扑
- 计算拓扑变化
- 推送变化到前端
- 写入链路延迟日志
- 判断是否进入 fast mode

特点：

- 只基于缓存快照工作
- 生成最终逻辑拓扑
- 只推送变化部分，避免前端全量重绘
- 日志记录以 publisher 最终状态为准，而不是 worker 的原始瞬时结果

## 3.4 前端页面

负责：

- 显示当前拓扑
- 展示节点状态和链路延迟
- 标识连接状态
- 显示最后更新时间
- 显示当前刷新模式
- 保存节点拖动后的位置
- 在 stale / disconnected 时降低拓扑透明度

---

# 4. 配置文件

所有配置来自：

```text
config.json
```

当前版本推荐结构如下：

```json
{
  "worker_interval_normal": 3,
  "worker_interval_fast": 1,

  "publish_interval_normal": 2,
  "publish_interval_fast": 1,

  "fast_mode_hold_seconds": 10,

  "ping_count": 1,
  "ping_timeout_ms": 1000,
  "offline_grace": 2,

  "latency_warning_enter_ms": 50,
  "latency_warning_exit_ms": 45,

  "latency_critical_enter_ms": 150,
  "latency_critical_exit_ms": 140,
  "latency_warning_critical_description": "warning和critical状态的切换不是根据同一个延迟阈值进行，而是错开，防止状态在阈值附近频繁跳变",

  "stale_timeout_ms": 10000,

  "latency_log": {
    "enabled": true,
    "log_only_when_changed": false,
    "latency_change_threshold_ms": 5,
    "log_description": "log_only_when_changed=false 表示记录所有链路，true 表示仅记录发生变化的链路; latency_change_threshold_ms=5 表示当延迟的抖动超过5ms时，就认为链路发生变化",
    "dir": "/var/log/lan",
    "filename": "网络延迟日志.csv",
    "encoding": "utf-8-sig",
    "max_file_size_mb": 50,
    "delete_when_exceed": true
  },

  "service_host": "0.0.0.0",
  "service_port": 15201,

  "deploy_device": {
    "192.168.0.10": "中心节点"
  },

  "devices": {
    "192.168.0.11": "主机1",
    "192.168.0.12": "主机2",
    "192.168.0.13": "主机3",
    "192.168.0.14": "主机4",
    "192.168.0.15": "主机5",
    "192.168.0.16": "主机6",
    "192.168.0.17": "主机7-无线"
  }
}
```

以上结构对应当前代码实现。

---

# 5. 配置项说明

## 5.1 Worker 与 Publisher

| 配置项                  | 含义                                    |
| ----------------------- | --------------------------------------- |
| worker_interval_normal  | 正常模式下 worker 探测周期              |
| worker_interval_fast    | 快速模式下 worker 探测周期              |
| publish_interval_normal | 正常模式下 publisher 推送周期           |
| publish_interval_fast   | 快速模式下 publisher 推送周期           |
| fast_mode_hold_seconds  | 异常恢复后，fast 模式继续保持的最短时间 |

当前代码中 worker 和 publisher 都会在循环中动态读取当前模式对应的周期，因此模式切换会直接影响下一轮探测与发布频率。

## 5.2 Ping 参数

| 配置项          | 含义                          |
| --------------- | ----------------------------- |
| ping_count      | 每次 ping 发包数量            |
| ping_timeout_ms | 每次 ping 的超时时间，单位 ms |
| offline_grace   | 连续失败多少次后才判定为离线  |

对应 `monitor.py` 中 `ping_host()` 与 `ping_many()` 的探测逻辑。

## 5.3 状态阈值

| 配置项                    | 含义                 |
| ------------------------- | -------------------- |
| latency_warning_enter_ms  | 进入 warning 的阈值  |
| latency_warning_exit_ms   | 退出 warning 的阈值  |
| latency_critical_enter_ms | 进入 critical 的阈值 |
| latency_critical_exit_ms  | 退出 critical 的阈值 |

以上四个参数共同定义迟滞状态机。

## 5.4 前端数据新鲜度

| 配置项           | 含义                                       |
| ---------------- | ------------------------------------------ |
| stale_timeout_ms | 前端超过多久未收到更新时，判定为“数据过期” |

当前版本后端会将该值注入前端模板，前端按该值执行 stale 判断。

## 5.5 日志配置

| 配置项                      | 含义                                                              |
| --------------------------- | ----------------------------------------------------------------- |
| enabled                     | 是否启用日志                                                      |
| log_only_when_changed       | false 表示每个发布周期记录全部链路；true 表示只记录发生变化的链路 |
| latency_change_threshold_ms | 仅记录变化时，延迟变化达到多少 ms 才算变化                        |
| dir                         | 用户期望写入的日志目录                                            |
| filename                    | 日志文件名                                                        |
| encoding                    | 日志编码，推荐 utf-8-sig                                          |
| max_file_size_mb            | 日志文件大小上限                                                  |
| delete_when_exceed          | 超限后是否删除旧日志并重建                                        |

当前版本日志目录支持权限检测与回退机制，因此 `dir` 表示“首选目录”，不是保证最终一定使用的目录。

## 5.6 服务配置

| 配置项       | 含义         |
| ------------ | ------------ |
| service_host | 服务监听地址 |
| service_port | 服务监听端口 |

用途：

- 控制 Flask-SocketIO 服务监听
- 支持不同主机、不同网络协议环境
- 支持 IPv4 / IPv6 部署场景切换

## 5.7 拓扑静态定义

| 配置项        | 含义                      |
| ------------- | ------------------------- |
| deploy_device | 部署 Flask 服务的中心主机 |
| devices       | 所有被监测设备            |

`deploy_device` 当前代码假定只有一个中心节点，使用该字典的首个键值对作为中心设备。

---

# 6. 拓扑结构

拓扑采用 **星型结构**：

```text
           device1
             |
device2 — deploy_device — device3
             |
           device4
```

中心节点：

```text
deploy_device
```

其余设备：

```text
devices
```

每个设备与中心节点有一条边。当前版本中所有边均为：

```text
deploy_device → 目标节点
```

不会出现设备之间的横向边。

---

# 7. 节点显示规则

每个节点显示：

```text
设备名称
IP地址
```

例如：

```text
主机1
192.168.0.11
```

节点 label 由后端生成，也可在前端兜底生成。当前逻辑如下：

- 若 name 和 ip 都存在，则显示两行
- 若仅有 name，则显示 name
- 其他情况显示空字符串

---

# 8. 节点角色

当前版本节点新增 `role` 字段，用于区分中心节点与普通节点。

取值：

- `deploy`：中心节点
- `device`：普通节点

作用：

- 前端样式选择器区分
- 中心节点尺寸、颜色与普通节点不同
- 语义上明确中心节点与被监测节点不是同类对象

---

# 9. 节点图标

系统支持透明 SVG 图标。

目录：

```text
static/icons/
```

文件名规则：

```text
设备名称.svg
```

示例：

```text
static/icons/主机1.svg
static/icons/主机7-无线.svg
```

显示方式：

- 图标叠加在节点上
- 使用 `background-fit: none`
- 图标不覆盖节点状态颜色
- 节点底盘颜色表示状态
- 图标表示设备类型

当前前端若节点数据中未显式携带 `icon`，会自动按 `name` 推导：

```text
/static/icons/<主机名称>.svg
```

因此后端无需为每个节点单独下发图标路径。

---

# 10. 中心节点样式

中心节点是 `deploy_device`，语义不同于普通设备节点。

中心节点特点：

- 作为拓扑中心节点
- 作为所有链路的源节点
- 前端视觉上与普通设备节点区分
- 节点 role 固定为 `deploy`

当前中心节点样式特征：

- 使用独立颜色：`#0EA5A4`
- 尺寸略大于普通节点
- 通过前端选择器 `node[role = 'deploy']` 应用样式
- 默认状态显示为 `normal`
- 不参与普通设备的告警/离线问题判断逻辑

当前前端样式：

- 普通节点：56 × 56
- 中心节点：72 × 72

---

# 11. 链路显示规则

每条边表示：

```text
deploy_device → 目标节点
```

边上显示当前延迟：

- 有效延迟：`xx.xx ms`
- 无有效延迟：`--`

显示要求：

- 标签沿边旋转显示
- 标签背景透明
- 延迟保留两位小数
- 边状态与目标节点状态一致

当前实现中：

- 后端会生成 `latency_label`
- 前端若缺失该字段，也可根据 `latency` 自动生成标签
- 当设备 offline 时，边标签显示 `--`
- offline 边使用灰色虚线显示

---

# 12. 状态集合

系统定义四种状态：

```text
normal
warning
critical
offline
```

其中：

- `offline` 由连续失败判定
- `normal / warning / critical` 由延迟判定
- 中心节点不参与告警判定，始终展示为中心角色节点

---

# 13. 离线判定与离线防抖

离线检测采用 **失败计数机制**。

配置：

```text
offline_grace
```

规则：

```text
连续 ping 失败 ≥ offline_grace
→ 状态 = offline
```

未达到门限前：

- 保留上一次成功延迟
- 保留上一次状态
- 不立即显示离线

此机制用于避免偶发丢包导致界面与日志抖动。

当前缓存中每个设备维护：

- `fail_count`
- `latency`
- `status`

当探测恢复成功时：

- `fail_count` 归零
- 使用最新延迟重新进入迟滞状态判断逻辑

---

# 14. 状态迟滞机制 (Hysteresis)

## 14.1 设计目的

在网络监控系统中，如果状态判定只依赖单一阈值，当延迟在阈值附近波动时，会出现频繁的状态切换。

例如：

```text
49.8 ms
50.2 ms
49.9 ms
50.3 ms
```

如果 warning 阈值为 50 ms，系统状态可能出现：

```text
normal → warning → normal → warning → normal
```

这种现象会导致：

- 前端节点颜色频繁闪烁
- fast mode 被反复触发
- 日志记录大量无意义变化
- 用户难以判断真实网络状态

因此系统采用 **状态迟滞机制（Hysteresis）** 来避免这种问题。

## 14.2 迟滞原理

迟滞机制通过 **不同的进入阈值和退出阈值** 来稳定状态变化。

核心思想：

```text
进入状态的阈值 ≠ 退出状态的阈值
```

例如：

```text
warning_enter = 50 ms
warning_exit  = 45 ms
```

这意味着：

- 延迟 ≥ 50 ms 才进入 `warning`
- 延迟 ≤ 45 ms 才恢复 `normal`

这样在 `45~50 ms` 的区间内，状态保持稳定。

## 14.3 配置参数

迟滞阈值通过以下配置控制：

```json
{
  "latency_warning_enter_ms": 50,
  "latency_warning_exit_ms": 45,
  "latency_critical_enter_ms": 150,
  "latency_critical_exit_ms": 140
}
```

## 14.4 状态转换规则

### normal → warning

条件：

```text
latency ≥ warning_enter
```

### warning → normal

条件：

```text
latency ≤ warning_exit
```

### warning → critical

条件：

```text
latency ≥ critical_enter
```

### critical → warning

条件：

```text
latency < critical_exit
```

### critical → normal

当前实现中，critical 状态下若延迟小于 `CRIT_EXIT`，会先判断是否仍高于 `WARN_ENTER`：

- 若仍高于 `WARN_ENTER`，则回到 `warning`
- 若已低于 `WARN_ENTER`，则直接回到 `normal`

## 14.5 状态机示意

```text
             latency ≥ critical_enter
warning ------------------------→ critical
   ↑                               |
   | latency < critical_exit       |
   |                               ↓
normal ←------------------------ warning
       latency ≤ warning_exit
```

## 14.6 迟滞区间

### normal / warning 迟滞区间

```text
45 ms ~ 50 ms
```

在该区间内：

- 如果当前是 normal，则保持 normal
- 如果当前是 warning，则保持 warning

### warning / critical 迟滞区间

```text
140 ms ~ 150 ms
```

在该区间内：

- 如果当前是 warning，则保持 warning
- 如果当前是 critical，则保持 critical

## 14.7 实际效果

迟滞机制带来的效果：

- 前端状态更稳定
- fast mode 更稳定
- 日志更干净
- 更符合真实网络状态

## 14.8 与离线判定的关系

状态迟滞 **不影响离线判定**。

离线判定由：

```text
offline_grace
```

控制，而迟滞机制只作用于：

```text
normal / warning / critical
```

## 14.9 总结

系统状态判定采用两层稳定机制：

第一层：

```text
offline_grace
```

避免偶发丢包导致离线。

第二层：

```text
hysteresis
```

避免延迟边界抖动导致状态闪烁。

---

# 15. 页面配色

## 15.1 页面背景

```text
#F5F7FA
```

## 15.2 Toolbar

```text
#C9D2DF
```

## 15.3 节点状态颜色

| 状态     | 颜色    |
| -------- | ------- |
| normal   | #10B981 |
| warning  | #F5A623 |
| critical | #E74C3C |
| offline  | #9AA0A6 |

## 15.4 中心节点颜色

```text
#0EA5A4
```

## 15.5 边颜色

| 状态     | 颜色            |
| -------- | --------------- |
| normal   | #10B981         |
| warning  | #F5A623         |
| critical | #E74C3C         |
| offline  | #9AA0A6（虚线） |

---

# 16. 页面布局与文字样式

当前页面由两部分构成：

- 顶部 toolbar
- 下方全屏拓扑区域

toolbar 高度：

```text
40px
```

toolbar 字体大小：

```text
18px
```

拓扑容器高度：

```text
100vh - 40px
```

相比旧版本，当前 toolbar 字号更大，以增强现场可读性。

---

# 17. 页面更新机制

## 17.1 Worker 层

每个设备一个 worker：

```text
ping → 写缓存
```

特点：

- 每个目标 IP 一个独立 worker
- 各 worker 互不等待
- 某个设备离线不会拖慢其他设备

## 17.2 Publisher 层

publisher 周期：

```text
读取缓存
生成拓扑
计算变化
推送变化
```

只推送变化部分，避免前端全量重绘。

## 17.3 前端层

前端监听两个事件：

- `snapshot`
- `update`

规则：

- 首次连接后端发送 `snapshot`
- 后续周期性变化发送 `update`
- 前端只应用收到的节点/边变化，不重新初始化整个图

这一机制减少重绘并保留拖动后的节点位置。

---

# 18. 首次页面加载

服务启动时：

```text
init_latency_log()
↓
warm_up_cache()
↓
生成拓扑缓存
↓
index() 返回缓存
```

因此用户首次打开页面时无需等待第一轮 ping。

与旧版本相比，当前版本在 warm up 之前会先初始化日志系统。

---

# 19. 自适应模式

系统支持两种模式：

```text
normal
fast
```

## 19.1 normal

- 正常探测频率
- 节省资源

## 19.2 fast

当出现以下任一状态时进入：

```text
warning
critical
offline
```

进入 fast 后：

- worker 探测加快
- publisher 推送加快

异常恢复后：

```text
fast_mode_hold_seconds
```

到期后回到 normal。

## 19.3 当前版本模式显示

当前版本后端输出给前端的模式名称为中文：

- `正常`
- `快速`

而非旧版的 `normal` / `fast`。toolbar 会直接显示该中文文本。

---

# 20. 前端实时状态

前端页面状态包括：

| 状态         | 含义     |
| ------------ | -------- |
| live         | 实时     |
| stale        | 数据过期 |
| disconnected | 服务断开 |

判定规则：

```text
socket disconnect → disconnected
超时无更新 → stale
收到 snapshot/update → live
```

表现方式：

- toolbar 显示当前连接状态
- stale / disconnected 时拓扑整体降透明度
- 服务恢复后自动 reconnect

## 20.1 stale_timeout 实现

当前版本中：

- 后端从配置读取 `stale_timeout_ms`
- 将该值注入模板
- 前端用定时器每秒检查一次：

```text
Date.now() - lastDataAt > STALE_TIMEOUT_MS
→ stale
```

这是当前实现与旧 Spec 相比更加明确的部分。

---

# 21. 时间显示规则

当前版本中 topology 的时间戳格式为：

```text
YYYY-MM-DD HH:MM:SS
```

而不是旧版的仅显示时分秒。

该时间将用于：

- toolbar 最后更新时间显示
- 各类后台日志输出
- 便于运维排障与跨天运行观察

---

# 22. 日志系统

日志记录链路延迟。

文件名示例：

```text
网络延迟日志.csv
```

编码：

```text
utf-8-sig
```

CSV 表头：

```text
时间,源IP,源设备,目标IP,目标设备,延迟(ms),状态
```

状态值：

```text
正常
告警
严重
离线
```

日志记录来源：

- 记录基于 **publisher 最终输出的链路状态**
- 不是 worker 原始瞬时结果

---

# 23. 日志记录策略

配置：

```text
log_only_when_changed
```

## 23.1 false

每个发布周期记录全部链路。

## 23.2 true

只记录变化链路。

变化定义：

- 状态变化
- 延迟由无 → 有
- 延迟由有 → 无
- 延迟变化绝对值 ≥ `latency_change_threshold_ms`

说明：

- 该规则用于抑制小幅延迟抖动造成的无意义日志
- 状态变化仍然会被记录

实现方式：

- publisher 保存 `last_logged_topology`
- 对比当前边数据与上一轮已记录边数据
- 根据 `edge_changed_for_log()` 决定是否写入该链路

---

# 24. 日志记录的数据来源

日志记录基于 publisher 生成的最终链路状态。

该状态已经经过：

- 离线防抖 (`offline_grace`)
- 状态迟滞 (`hysteresis`)

处理，因此日志不会记录由阈值边界抖动产生的频繁状态切换。

---

# 25. 日志文件控制

写入前检查文件大小。

如果：

```text
file_size > max_file_size_mb
```

且：

```text
delete_when_exceed = true
```

则：

```text
删除旧日志
重新创建文件
重新写入表头
```

当前默认配置上限已从旧版常见示例的 20MB 调整为 50MB。实际以 `config.json` 为准。

---

# 26. 日志写入安全

日志写入后执行：

```text
flush()
fsync()
```

目的：

- 减少系统突然断电时日志丢失的概率
- 降低日志损坏风险
- 即使异常中断，通常最多只影响最后一行

日志表头写入也执行同样的安全刷盘逻辑。

---

# 27. 日志目录初始化与权限容错

这是 v1.3 新增的重要能力。

## 27.1 设计目标

在实际部署中，用户可能将日志目录配置为：

```text
/var/log/lan
```

但运行进程未必具备写入权限。为了避免日志失败影响主监控功能，系统新增日志初始化与容错机制。

## 27.2 初始化流程

服务启动时执行：

```text
init_latency_log()
```

逻辑流程：

```text
若 enabled = false
→ 不启用日志

若 enabled = true
→ 优先尝试 config.json 中 latency_log.dir
→ 若失败，尝试 fallback 目录 "logs"
→ 若仍失败，禁用日志功能
```

## 27.3 fallback 目录

当前固定 fallback 目录为：

```text
logs
```

说明：

- 当配置目录不可写时，系统会自动尝试使用当前工作目录下的 `logs/`
- 若 fallback 成功，程序继续记录日志
- 若 fallback 失败，程序继续运行但不再写日志

## 27.4 初始化时的测试方式

初始化时会：

- `os.makedirs(log_dir, exist_ok=True)`
- 构造测试路径
- 以追加模式打开日志文件
- 若成功，则认为目录可用
- 写入或确认表头存在

## 27.5 运行期禁用日志

如果程序运行过程中写日志失败，则执行：

```text
disable_latency_log_runtime()
```

效果：

- `LATENCY_LOG_RUNTIME_ENABLED = False`
- `LATENCY_LOG_PATH = None`
- 后续不再尝试写日志
- 主监控逻辑继续正常运行

这一机制确保：

> 日志失败 ≠ 监控系统失败。

---

# 28. 后台日志输出规范

当前版本后台输出统一使用带时间戳的文本日志格式：

```text
YYYY-MM-DD HH:MM:SS [LEVEL] message
```

示例：

```text
2026-03-11 20:11:23 [INFO] publisher loop started
2026-03-11 20:11:23 [INFO] ping worker started: 10.100.25.121
2026-03-11 20:11:24 [WARN] log dir unavailable: /var/log/lan, error: ...
```

当前代码中主要输出级别包括：

- `[INFO]`
- `[WARN]`

用途：

- 便于控制台观察
- 便于 systemd journal 收集
- 便于部署环境下排障

---

# 29. 客户端连接日志

当前版本新增客户端连接与断开日志。

记录时机：

- SocketIO `connect`
- SocketIO `disconnect`

记录内容包括客户端 IP。

IP 获取顺序：

1. `X-Forwarded-For`
2. `request.remote_addr`

因此系统支持：

- 直连访问
- 经反向代理转发后的访问来源记录

---

# 30. 服务运行与监听

当前版本服务监听地址与端口不再写死，而是通过配置控制：

```json
"service_host": "0.0.0.0",
"service_port": 15021
```

优点：

- 部署更灵活
- 支持不同网卡/不同协议环境
- 支持 IPv6-only 环境下将监听地址改为 `::`

这部分是 v1.3 相比 v1.2 的重要运行时增强。

---

# 31. 服务运行与恢复

推荐使用 `systemd` 管理服务。

目标：

- 服务崩溃后自动拉起
- 前端无需手动刷新即可自动恢复
- 日志目录可通过 systemd 配套机制管理
- 便于 journal 集中查看运行日志

典型流程：

```text
服务异常退出
↓
systemd 自动重启
↓
前端检测 disconnect
↓
服务恢复后自动 reconnect
↓
页面恢复 live
```

---

# 32. 当前版本功能总结

当前系统已经实现：

- 实时拓扑监控
- 多客户端访问
- SVG 图标节点
- 中心节点角色字段与专属样式
- fast / normal 自适应模式
- 离线防抖
- 状态迟滞 (hysteresis)
- 前端实时性检测
- stale_timeout 配置化
- CSV 中文日志
- 延迟变化阈值控制
- 日志目录权限检测
- 日志目录自动 fallback
- 日志运行时禁用机制
- 客户端连接 / 断开日志
- 服务监听地址与端口配置化
- Linux x86 / ARM 部署适配能力

---

# 33. 未来可能扩展

可能增加：

- 日志按日期分文件
- 状态变化冷却时间
- 历史延迟统计图
- Web 告警系统
- 拓扑自动布局优化
- 更丰富的事件日志
- 服务端状态接口
- Web 配置管理页面
- 日志轮转与归档机制
