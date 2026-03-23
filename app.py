# -*- encoding: utf-8 -*-

import copy
import csv
import io
import json
import os
import threading
import time
import sys
import socket
import subprocess

from datetime import datetime
from typing import Optional

os.environ["EVENTLET_NO_GREENDNS"] = "yes"
import eventlet

eventlet.monkey_patch()

from flask import Flask, jsonify, render_template, request
from flask_socketio import SocketIO, emit

from monitor import ping_host, ping_many


def resource_path(relative_path):
    base_path = getattr(sys, "_MEIPASS", os.path.abspath("."))
    return os.path.join(base_path, relative_path)

def get_local_ipv4_addresses():
    ipv4s = set()
    
    # 方式1：优先使用 ip命令获取所有全局IPv4
    try:
        result = subprocess.run(["ip", "-4", "-o", "addr", "show", "scope", "global"], capture_output=True, text=True, check=True,)
        for line in result.stdout.splitlines():
            parts = line.split()
            # 典型格式：2: the0     inte 192.168.1.10/24 brd ...
            if "inet" in parts:
                idx = parts.index("inet")
                ip_with_mask = parts[idx + 1]
                ip = ip_with_mask.split("/")[0]
                
                if ip and not ip.startswith("127."):
                    ipv4s.add(ip)
    except Exception:
        pass
    
    # 方式2：使用 socket 方式补充
    try:
        hostname = socket.gethostname()
        for item in socket.getaddrinfo(hostname, None, socket.AF_INET, socket.SOCK_STREAM):
            ip = item[4][0]
            if ip and not ip.startswith("127."):
                ipv4s.add(ip)
    except Exception:
        pass

    return sorted(ipv4s)


def ensure_deploy_ip_matches_local_host():
    deploy_ip = list(deploy_device.keys())[0]
    local_ipv4s = get_local_ipv4_addresses()
    
    if deploy_ip not in local_ipv4s:
        raise RuntimeError(f"服务启动失败：配置文件中的部署节点地址 {deploy_ip} 不是当前主机的IPv4地址 {local_ipv4s}，请检查！")


def load_config_file(config_path):
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        raise FileNotFoundError(f"配置文件加载失败：未找到配置文件 {config_path}。")
    except json.JSONDecodeError as e:
        raise RuntimeError(f"配置文件加载失败：config.json 不是合法的JSON格式。"
                          f"第 {e.lineno} 行，第 {e.colno} 列附近存在语法错误。")
    except OSError as e:
        raise RuntimeError(f"配置文件加载失败：无法读取配置文件。{e}")


def validate_config(cfg:dict):
    if not isinstance(cfg, dict):
        raise RuntimeError("配置文件加载失败：顶层结构必须是JSON对象。")
                           
    required_keys = [
        "service_host",
        "service_port",
        "deploy_device",
        "devices",
    ]
    
    for key in required_keys:
        if key not in cfg:
            raise RuntimeError(f"配置文件加载失败，缺少必要配置项:{key}。")
    
    if not isinstance(cfg["service_host"], str) or not cfg["service_host"].strip():
        raise RuntimeError("配置文件加载失败：service_host 必须是非空字符串。")
        
    if not isinstance(cfg["service_port"], int):
        raise RuntimeError("配置文件加载失败：service_port 必须是整数。")

    if not isinstance(cfg["deploy_device"], dict) or len(cfg["deploy_device"]) != 1:
        raise RuntimeError("配置文件加载失败：deploy_device 必须是仅有一个元素的字典。")
    
    if not isinstance(cfg["devices"], dict) or not cfg["devices"]:
        raise RuntimeError("配置文件加载失败：devices 必须是非空字典。")
        
        
app = Flask(__name__, template_folder=resource_path("templates"), static_folder=resource_path("static"))
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")

CONFIG_PATH = resource_path("config.json")
                           
try:
    config = load_config_file(CONFIG_PATH)
    validate_config(config)
except RuntimeError as e:
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [ERROR] {e}")
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [INFO] 请修正 {CONFIG_PATH} 后重新启动程序。")
    raise SystemExit(1)
except FileNotFoundError as e:
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [ERROR] {e}")
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [INFO] 请检查配置文件路径后重新启动程序。")
    raise SystemExit(1)

SERVICE_HOST = config["service_host"]
SERVICE_PORT = config["service_port"]

deploy_device = config["deploy_device"]
devices = config["devices"]

WORKER_INTERVAL_NORMAL = config.get("worker_interval_normal", 3)
WORKER_INTERVAL_FAST = config.get("worker_interval_fast", 1)

PUBLISH_INTERVAL_NORMAL = config.get("publish_interval_normal", 2)
PUBLISH_INTERVAL_FAST = config.get("publish_interval_fast", 1)

FAST_MODE_HOLD_SECONDS = config.get("fast_mode_hold_seconds", 10)

PING_COUNT = config.get("ping_count", 1)
PING_TIMEOUT_MS = config.get("ping_timeout_ms", 1000)
OFFLINE_GRACE = config.get("offline_grace", 2)

WARN_ENTER = config.get("latency_warning_enter_ms", 50)
WARN_EXIT = config.get("latency_warning_exit_ms", 45)

CRIT_ENTER = config.get("latency_critical_enter_ms", 150)
CRIT_EXIT = config.get("latency_critical_exit_ms", 140)

STALE_TIMEOUT_MS = config.get("stale_timeout_ms", 10000)

LATENCY_LOG_CONFIG = config.get("latency_log", {})
LATENCY_LOG_ENABLED = LATENCY_LOG_CONFIG.get("enabled", False)
LOG_ONLY_WHEN_CHANGED = LATENCY_LOG_CONFIG.get("log_only_when_changed", False)
LATENCY_CHANGE_THRESHOLD_MS = float(LATENCY_LOG_CONFIG.get("latency_change_threshold_ms", 5))
LATENCY_LOG_DIR = LATENCY_LOG_CONFIG.get("dir", "logs")
LATENCY_LOG_FILENAME = LATENCY_LOG_CONFIG.get("filename", "延迟日志.csv")
LATENCY_LOG_ENCODING = LATENCY_LOG_CONFIG.get("encoding", "utf-8-sig")
LATENCY_LOG_MAX_FILE_SIZE_MB = LATENCY_LOG_CONFIG.get("max_file_size_mb", 20)
LATENCY_LOG_DELETE_WHEN_EXCEED = LATENCY_LOG_CONFIG.get("delete_when_exceed", True)

LATENCY_LOG_RUNTIME_ENABLED = False
LATENCY_LOG_PATH = None
LATENCY_LOG_FALLBACK_DIR = "logs"

HISTORY_ANALYSIS_CONFIG = config.get("history_analysis", {})
HISTORY_ANALYSIS_ENABLED = HISTORY_ANALYSIS_CONFIG.get("enabled", True)
HISTORY_ANALYSIS_MAX_HOURS = int(HISTORY_ANALYSIS_CONFIG.get("max_hours", 24))
HISTORY_ANALYSIS_MAX_FILE_SIZE_MB = int(
    HISTORY_ANALYSIS_CONFIG.get("max_file_size_mb", LATENCY_LOG_MAX_FILE_SIZE_MB)
)
HISTORY_ANALYSIS_MAX_ROWS = int(HISTORY_ANALYSIS_CONFIG.get("max_rows", 250000))
HISTORY_ANALYSIS_WIRELESS_DEVICE_NAMES = set(
    HISTORY_ANALYSIS_CONFIG.get("wireless_device_names", ["无人机-无线"])
)

CSV_HEADER = ["时间", "源IP", "源设备", "目标IP", "目标设备", "延迟(ms)", "状态"]
HISTORY_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"

deploy_ip = list(deploy_device.keys())[0]
deploy_name = deploy_device[deploy_ip]

results_lock = threading.Lock()
latest_results = {}

topology_lock = threading.Lock()
current_topology = {
    "nodes": [],
    "edges": [],
    "timestamp": "",
    "mode": "正常"
}

mode_lock = threading.Lock()
fast_mode_until = 0.0

latency_log_lock = threading.Lock()


# ---------- runtime topology ----------
def status_with_hysteresis(lat, old_status):
    if lat is None:
        return "offline"

    if old_status == "critical":
        if lat >= CRIT_EXIT:
            return "critical"
        if lat >= WARN_ENTER:
            return "warning"
        return "normal"

    if old_status == "warning":
        if lat >= CRIT_ENTER:
            return "critical"
        if lat > WARN_EXIT:
            return "warning"
        return "normal"

    if lat >= CRIT_ENTER:
        return "critical"
    if lat >= WARN_ENTER:
        return "warning"
    return "normal"


def format_node_label(name, ip):
    if name and ip:
        return f"{name}\n{ip}"
    if name:
        return name
    return ""


def format_latency_label(lat):
    if isinstance(lat, (int, float)):
        return f"{lat:.2f} ms"
    return "--"


def get_all_probe_ips():
    return list(devices.keys())


def enter_fast_mode():
    global fast_mode_until
    with mode_lock:
        fast_mode_until = max(fast_mode_until, time.time() + FAST_MODE_HOLD_SECONDS)


def is_fast_mode():
    with mode_lock:
        return time.time() < fast_mode_until


def get_worker_interval():
    return WORKER_INTERVAL_FAST if is_fast_mode() else WORKER_INTERVAL_NORMAL


def get_publish_interval():
    return PUBLISH_INTERVAL_FAST if is_fast_mode() else PUBLISH_INTERVAL_NORMAL


def write_result(ip, latency):
    with results_lock:
        old = latest_results.get(ip, {
            "latency": None,
            "status": "offline",
            "updated_at": 0.0,
            "fail_count": 0
        })

        if latency is None:
            fail_count = old.get("fail_count", 0) + 1
            if fail_count >= OFFLINE_GRACE:
                st = "offline"
                final_latency = None
            else:
                st = old.get("status", "offline")
                final_latency = old.get("latency")
        else:
            fail_count = 0
            old_status = old.get("status", "offline")
            st = status_with_hysteresis(latency, old_status)
            final_latency = latency

        latest_results[ip] = {
            "latency": final_latency,
            "status": st,
            "updated_at": time.time(),
            "fail_count": fail_count
        }


def read_results_snapshot():
    with results_lock:
        return copy.deepcopy(latest_results)


def build_topology_from_snapshot(snapshot):
    nodes = []
    edges = []

    nodes.append({
        "data": {
            "id": deploy_ip,
            "name": deploy_name,
            "ip": deploy_ip,
            "status": "normal",
            "role": "deploy",
            "label": format_node_label(deploy_name, deploy_ip)
        }
    })

    for ip, name in devices.items():
        item = snapshot.get(ip, {})
        lat = item.get("latency")
        st = item.get("status", "offline")

        nodes.append({
            "data": {
                "id": ip,
                "name": name,
                "ip": ip,
                "status": st,
                "role": "device",
                "label": format_node_label(name, ip)
            }
        })

        edges.append({
            "data": {
                "id": f"edge_{ip}",
                "source": deploy_ip,
                "target": ip,
                "latency": lat,
                "latency_label": format_latency_label(lat),
                "status": st
            }
        })

    return {
        "nodes": nodes,
        "edges": edges,
        "timestamp": time.strftime(HISTORY_TIME_FORMAT),
        "mode": "快速" if is_fast_mode() else "正常"
    }


def rebuild_topology_cache():
    snapshot = read_results_snapshot()
    topology = build_topology_from_snapshot(snapshot)

    with topology_lock:
        current_topology["nodes"] = topology["nodes"]
        current_topology["edges"] = topology["edges"]
        current_topology["timestamp"] = topology["timestamp"]
        current_topology["mode"] = topology["mode"]

    return topology


def topology_to_maps(topology):
    node_map = {}
    edge_map = {}

    for node in topology.get("nodes", []):
        data = node.get("data", {})
        node_id = data.get("id")
        if node_id is not None:
            node_map[node_id] = data

    for edge in topology.get("edges", []):
        data = edge.get("data", {})
        edge_id = data.get("id")
        if edge_id is not None:
            edge_map[edge_id] = data

    return node_map, edge_map


def edge_changed_for_log(new_edge_data, old_edge_data):
    if old_edge_data is None:
        return True

    new_status = new_edge_data.get("status")
    old_status = old_edge_data.get("status")
    if new_status != old_status:
        return True

    new_latency = new_edge_data.get("latency")
    old_latency = old_edge_data.get("latency")

    if (new_latency is None) != (old_latency is None):
        return True
    if new_latency is None and old_latency is None:
        return False
    return abs(float(new_latency) - float(old_latency)) >= LATENCY_CHANGE_THRESHOLD_MS


def select_edges_for_log(topology, last_logged_topology):
    if not LOG_ONLY_WHEN_CHANGED:
        return topology.get("edges", [])
    if last_logged_topology is None:
        return topology.get("edges", [])

    _, old_edge_map = topology_to_maps(last_logged_topology)
    changed_edges = []
    for edge in topology.get("edges", []):
        data = edge.get("data", {})
        edge_id = data.get("id")
        old_data = old_edge_map.get(edge_id)
        if edge_changed_for_log(data, old_data):
            changed_edges.append(edge)
    return changed_edges


def build_delta(new_topology, old_topology):
    new_node_map, new_edge_map = topology_to_maps(new_topology)

    if old_topology is None:
        changed_nodes = [{"data": copy.deepcopy(v)} for v in new_node_map.values()]
        changed_edges = [{"data": copy.deepcopy(v)} for v in new_edge_map.values()]
    else:
        old_node_map, old_edge_map = topology_to_maps(old_topology)
        changed_nodes = []
        for node_id, data in new_node_map.items():
            if old_node_map.get(node_id) != data:
                changed_nodes.append({"data": copy.deepcopy(data)})
        changed_edges = []
        for edge_id, data in new_edge_map.items():
            if old_edge_map.get(edge_id) != data:
                changed_edges.append({"data": copy.deepcopy(data)})

    return {
        "nodes": changed_nodes,
        "edges": changed_edges,
        "timestamp": new_topology.get("timestamp", ""),
        "mode": new_topology.get("mode", "正常")
    }


def topology_has_problem(topology):
    for node in topology.get("nodes", []):
        data = node.get("data", {})
        node_id = data.get("id")
        if node_id == deploy_ip:
            continue
        if data.get("status") in ("warning", "critical", "offline"):
            return True
    return False


# ---------- latency log ----------
def status_to_cn(status):
    mapping = {
        "normal": "正常",
        "warning": "告警",
        "critical": "严重",
        "offline": "离线"
    }
    return mapping.get(status, str(status))


def get_latency_log_path() -> Optional[str]:
    if not LATENCY_LOG_RUNTIME_ENABLED:
        return None
    return LATENCY_LOG_PATH


def disable_latency_log_runtime():
    global LATENCY_LOG_RUNTIME_ENABLED, LATENCY_LOG_PATH
    LATENCY_LOG_RUNTIME_ENABLED = False
    LATENCY_LOG_PATH = None


def ensure_latency_log_header():
    if not LATENCY_LOG_RUNTIME_ENABLED:
        return
    path = get_latency_log_path()
    if not path:
        return
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return

    with open(path, "w", encoding=LATENCY_LOG_ENCODING, newline="") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_HEADER)
        f.flush()
        os.fsync(f.fileno())


def init_latency_log():
    global LATENCY_LOG_RUNTIME_ENABLED, LATENCY_LOG_PATH, LATENCY_LOG_DIR

    if not LATENCY_LOG_ENABLED:
        print(f"{time.strftime(HISTORY_TIME_FORMAT)} [INFO] latency log disabled by config")
        LATENCY_LOG_RUNTIME_ENABLED = False
        LATENCY_LOG_PATH = None
        return

    candidate_dirs = []
    if LATENCY_LOG_DIR:
        candidate_dirs.append(LATENCY_LOG_DIR)
    if LATENCY_LOG_FALLBACK_DIR not in candidate_dirs:
        candidate_dirs.append(LATENCY_LOG_FALLBACK_DIR)

    last_error = None
    for log_dir in candidate_dirs:
        try:
            os.makedirs(log_dir, exist_ok=True)
            test_path = os.path.join(log_dir, LATENCY_LOG_FILENAME)
            with open(test_path, "a", encoding=LATENCY_LOG_ENCODING, newline=""):
                pass

            LATENCY_LOG_DIR = log_dir
            LATENCY_LOG_PATH = test_path
            LATENCY_LOG_RUNTIME_ENABLED = True
            ensure_latency_log_header()

            if log_dir == candidate_dirs[0]:
                print(f"{time.strftime(HISTORY_TIME_FORMAT)} [INFO] 记录延时日志的功能开启: {LATENCY_LOG_PATH}")
            else:
                print(f"{time.strftime(HISTORY_TIME_FORMAT)} [WARN] 配置文件中分配的日志目录不可用, 日志将保存到: {LATENCY_LOG_PATH}")
            return
        except Exception as e:
            last_error = e
            print(f"{time.strftime(HISTORY_TIME_FORMAT)} [WARN] 日志目录无法使用: {log_dir}, 错误原因: {e}")

    LATENCY_LOG_RUNTIME_ENABLED = False
    LATENCY_LOG_PATH = None
    print(f"{time.strftime(HISTORY_TIME_FORMAT)} [WARN] 记录延时日志的功能关闭, 但是监控继续。")
    if last_error is not None:
        print(f"{time.strftime(HISTORY_TIME_FORMAT)} [WARN] last log init error: {last_error}")


def check_latency_log_file():
    if not LATENCY_LOG_ENABLED or not LATENCY_LOG_RUNTIME_ENABLED:
        return
    path = get_latency_log_path()
    if not path or not os.path.exists(path) or not LATENCY_LOG_DELETE_WHEN_EXCEED:
        return

    max_size_bytes = LATENCY_LOG_MAX_FILE_SIZE_MB * 1024 * 1024
    current_size = os.path.getsize(path)
    if current_size > max_size_bytes:
        os.remove(path)


def build_latency_log_rows(edge_list):
    now_str = time.strftime(HISTORY_TIME_FORMAT)
    rows = []
    for edge in edge_list:
        data = edge.get("data", {})
        target_ip = data.get("target", "")
        target_name = devices.get(target_ip, target_ip)
        latency = data.get("latency")
        status = status_to_cn(data.get("status", ""))
        latency_str = f"{latency:.2f}" if isinstance(latency, (int, float)) else ""
        rows.append([now_str, deploy_ip, deploy_name, target_ip, target_name, latency_str, status])
    return rows


def append_latency_log_rows(rows):
    if not LATENCY_LOG_ENABLED or not LATENCY_LOG_RUNTIME_ENABLED or not rows:
        return

    with latency_log_lock:
        try:
            check_latency_log_file()
            ensure_latency_log_header()
            path = get_latency_log_path()
            if not path:
                return
            with open(path, "a", encoding=LATENCY_LOG_ENCODING, newline="") as f:
                writer = csv.writer(f)
                writer.writerows(rows)
                f.flush()
                os.fsync(f.fileno())
        except Exception as e:
            print(f"{time.strftime(HISTORY_TIME_FORMAT)} [WARN] append latency log failed, disable logging: {e}")
            disable_latency_log_runtime()


def write_latency_log(topology, last_logged_topology):
    if not LATENCY_LOG_ENABLED:
        return
    edge_list = select_edges_for_log(topology, last_logged_topology)
    rows = build_latency_log_rows(edge_list)
    append_latency_log_rows(rows)


# ---------- history analysis ----------
def get_history_view_groups():
    normal_devices = []
    wireless_devices = []
    for _, name in devices.items():
        if name in HISTORY_ANALYSIS_WIRELESS_DEVICE_NAMES:
            wireless_devices.append(name)
        else:
            normal_devices.append(name)
    return {
        "normal_group": normal_devices,
        "wireless_group": wireless_devices,
    }


def parse_history_datetime(value: str) -> datetime:
    return datetime.strptime(value.strip(), HISTORY_TIME_FORMAT)


def get_uploaded_file_size(file_storage) -> int:
    stream = file_storage.stream
    current = stream.tell()
    stream.seek(0, os.SEEK_END)
    size = stream.tell()
    stream.seek(current)
    return size


def validate_history_file(file_storage):
    filename = (file_storage.filename or "").strip()
    if not filename.lower().endswith(".csv"):
        return False, "文件类型错误，请重新选择 CSV 文件。"

    file_size = get_uploaded_file_size(file_storage)
    max_size_bytes = HISTORY_ANALYSIS_MAX_FILE_SIZE_MB * 1024 * 1024
    if file_size > max_size_bytes:
        return False, f"文件大小超过{HISTORY_ANALYSIS_MAX_FILE_SIZE_MB}MB，请选择符合要求的文件。"
    return True, ""


def decode_csv_bytes(file_bytes: bytes) -> str:
    encodings = [LATENCY_LOG_ENCODING, "utf-8-sig", "utf-8", "gb18030"]
    for encoding in encodings:
        try:
            return file_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("E1:文件内容错误，请重新选择正确的文件。\n文件编码格式错误，解码出现异常。")


def parse_history_csv(file_bytes: bytes):
    text = decode_csv_bytes(file_bytes)
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        raise ValueError("E2:文件内容错误，请重新选择正确的文件。\n文件内容为空。")

    header = rows[0]
    if header != CSV_HEADER:
        raise ValueError("E3:文件内容错误，请重新选择正确的文件。\n文件表头内容错误。")

    if len(rows) <= 1:
        raise ValueError("E4:文件内容错误，请重新选择正确的文件。\n文件中没有有效数据。")

    parsed_rows = []
    valid_device_names = set(devices.values())
    for index, row in enumerate(rows[1:], start=2):
        if not row or not any(str(cell).strip() for cell in row):
            continue
        if len(row) != len(CSV_HEADER):
            raise ValueError(f"E5:文件内容错误，请重新选择正确的文件。\n第{index}行的内容与标题长度不匹配。")

        time_str, src_ip, src_name, target_ip, target_name, latency_str, status = [str(cell).strip() for cell in row]
        try:
            row_time = parse_history_datetime(time_str)
        except Exception as e:
            raise ValueError(f"E6:文件内容错误，请重新选择正确的文件。\n时间戳{time_str}不符合格式 {HISTORY_TIME_FORMAT} 要求。") from None

        if target_name not in valid_device_names:
            raise ValueError(f"E7:文件内容错误，请重新选择正确的文件。\n{target_name}不在目标主机{valid_device_names}范围内。")

        latency_value = None
        if latency_str != "":
            try:
                latency_value = float(latency_str)
            except Exception:
                raise ValueError(f"E8:文件内容错误，请重新选择正确的文件。\n延迟值 {latency_str} 转换为浮点数时错误。") from None

        parsed_rows.append({
            "time": row_time,
            "time_str": time_str,
            "source_ip": src_ip,
            "source_name": src_name,
            "target_ip": target_ip,
            "target_name": target_name,
            "latency": latency_value,
            "status": status,
            "line_no": index,
        })

    if not parsed_rows:
        raise ValueError("E9:文件内容错误，请重新选择正确的文件。\n解析数据时出现错误。")
    return parsed_rows


def count_history_rows(rows, start_time, end_time, view_type, selected_devices):
    groups = get_history_view_groups()
    allowed_devices = set(groups.get(view_type, []))
    if not allowed_devices:
        return 0

    if not selected_devices:
        raise ValueError("E1:请至少选择一条链路进行绘图。")

    selected_set = {name for name in selected_devices if name in allowed_devices}
    if not selected_set:
        raise ValueError("E2:请至少选择一条链路进行绘图。")

    count = 0
    for row in rows:
        if start_time <= row["time"] <= end_time and row["target_name"] in selected_set:
            count += 1
    return count


def build_history_series(rows, start_time, end_time, view_type, selected_devices):
    groups = get_history_view_groups()
    allowed_devices = set(groups.get(view_type, []))
    if not allowed_devices:
        return []

    if not selected_devices:
        raise ValueError("E3:请至少选择一条链路进行绘图。")

    selected_set = [name for name in selected_devices if name in allowed_devices]
    if not selected_set:
        raise ValueError("E4:请至少选择一条链路进行绘图。")

    filtered = [
        row for row in rows
        if start_time <= row["time"] <= end_time and row["target_name"] in selected_set
    ]
    if not filtered:
        return []

    series_map = {}
    for row in filtered:
        target_name = row["target_name"]
        if target_name not in series_map:
            series_map[target_name] = {
                "device_name": target_name,
                "link_name": f"{deploy_name}->{target_name}",
                "target_ip": row["target_ip"],
                "points": []
            }
        series_map[target_name]["points"].append([row["time_str"], row["latency"]])

    return [series_map[name] for name in selected_set if name in series_map]


def get_history_time_range(rows):
    if not rows:
        raise ValueError(f"E10:文件内容错误，请重新选择正确的文件。/n获取数据中的时间戳时出现异常。")
    min_time = min(row["time"] for row in rows)
    max_time = max(row["time"] for row in rows)
    return {
        "start_time": min_time.strftime(HISTORY_TIME_FORMAT),
        "end_time": max_time.strftime(HISTORY_TIME_FORMAT),
    }


# ---------- workers ----------
def ping_worker(ip):
    print(f"{time.strftime(HISTORY_TIME_FORMAT)} [INFO] ping worker started: {ip}")
    while True:
        try:
            latency = ping_host(ip, count=PING_COUNT, timeout=PING_TIMEOUT_MS)
            write_result(ip, latency)
        except Exception as e:
            print(f"{time.strftime(HISTORY_TIME_FORMAT)} [WARN] ping worker error [{ip}]: {e}")
            write_result(ip, None)
        socketio.sleep(get_worker_interval())


def publisher_loop():
    print(f"{time.strftime(HISTORY_TIME_FORMAT)} [INFO] publisher loop started")
    last_published = None
    last_logged_topology = None

    while True:
        try:
            topology = rebuild_topology_cache()
            if topology_has_problem(topology):
                enter_fast_mode()
                topology = rebuild_topology_cache()

            delta = build_delta(topology, last_published)
            write_latency_log(topology, last_logged_topology)
            last_logged_topology = copy.deepcopy(topology)

            if delta["nodes"] or delta["edges"] or (
                last_published is None or delta["mode"] != last_published.get("mode")
            ):
                socketio.emit("update", delta)

            last_published = copy.deepcopy(topology)
        except Exception as e:
            print(f"{time.strftime(HISTORY_TIME_FORMAT)} [WARN] publisher error: {e}")

        socketio.sleep(get_publish_interval())


def warm_up_cache():
    ips = get_all_probe_ips()
    results = ping_many(ips, count=PING_COUNT, timeout=PING_TIMEOUT_MS)
    for ip, latency in results.items():
        write_result(ip, latency)
    rebuild_topology_cache()


# ---------- routes ----------
@app.route("/")
def index():
    with topology_lock:
        topology = copy.deepcopy(current_topology)

    return render_template(
        "index.html",
        nodes=json.dumps(topology["nodes"], ensure_ascii=False),
        edges=json.dumps(topology["edges"], ensure_ascii=False),
        timestamp=topology["timestamp"],
        mode=topology["mode"],
        stale_timeout_ms=STALE_TIMEOUT_MS,
        history_analysis_enabled=HISTORY_ANALYSIS_ENABLED,
    )


@app.route("/history-analysis")
def history_analysis_page():
    groups = get_history_view_groups()
    link_label_map = {name: f"{deploy_name}->{name}" for name in devices.values()}
    return render_template(
        "history_analysis.html",
        history_analysis_enabled=HISTORY_ANALYSIS_ENABLED,
        max_hours=HISTORY_ANALYSIS_MAX_HOURS,
        max_file_size_mb=HISTORY_ANALYSIS_MAX_FILE_SIZE_MB,
        recommended_log_dir=LATENCY_LOG_DIR,
        default_filename=LATENCY_LOG_FILENAME,
        groups_json=json.dumps(groups, ensure_ascii=False),
        link_label_map_json=json.dumps(link_label_map, ensure_ascii=False),
        time_format=HISTORY_TIME_FORMAT,
    )


@app.route("/api/history-analysis/inspect", methods=["POST"])
def history_analysis_inspect():
    if not HISTORY_ANALYSIS_ENABLED:
        return jsonify({"ok": False, "message": "历史分析功能未启用。"}), 400

    file_storage = request.files.get("file")
    if file_storage is None or not (file_storage.filename or "").strip():
        return jsonify({"ok": False, "message": "请选择需要分析的 CSV 文件。"}), 400

    is_valid, message = validate_history_file(file_storage)
    if not is_valid:
        return jsonify({"ok": False, "message": message}), 400

    try:
        file_bytes = file_storage.read()
        rows = parse_history_csv(file_bytes)
        time_range = get_history_time_range(rows)
    except ValueError as e:
        return jsonify({"ok": False, "message": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "message": f"日志文件解析失败：{e}"}), 500

    return jsonify({
        "ok": True,
        "message": "",
        **time_range,
    })


@app.route("/api/history-analysis/analyze", methods=["POST"])
def history_analysis_analyze():
    if not HISTORY_ANALYSIS_ENABLED:
        return jsonify({"ok": False, "message": "历史分析功能未启用。"}), 400

    file_storage = request.files.get("file")
    if file_storage is None or not (file_storage.filename or "").strip():
        return jsonify({"ok": False, "message": "请选择需要分析的 CSV 文件。"}), 400

    is_valid, message = validate_history_file(file_storage)
    if not is_valid:
        return jsonify({"ok": False, "message": message}), 400

    start_time_raw = (request.form.get("start_time") or "").strip()
    end_time_raw = (request.form.get("end_time") or "").strip()
    view_type = (request.form.get("view_type") or "normal_group").strip()
    selected_devices_raw = (request.form.get("selected_devices") or "[]").strip()

    try:
        start_time = parse_history_datetime(start_time_raw)
        end_time = parse_history_datetime(end_time_raw)
    except Exception:
        return jsonify({"ok": False, "message": "时间范围输入错误，请检查后重新输入。"}), 400

    if start_time >= end_time:
        return jsonify({"ok": False, "message": "时间范围输入错误，请检查后重新输入。"}), 400

    hours_span = (end_time - start_time).total_seconds() / 3600.0
    if hours_span > HISTORY_ANALYSIS_MAX_HOURS:
        return jsonify({"ok": False, "message": f"所选时间范围超过{HISTORY_ANALYSIS_MAX_HOURS}小时，请修改后重试。"}), 400

    try:
        selected_devices = json.loads(selected_devices_raw)
        if not isinstance(selected_devices, list):
            raise ValueError
        selected_devices = [str(item).strip() for item in selected_devices if str(item).strip()]
    except Exception:
        return jsonify({"ok": False, "message": "链路选择参数错误，请重新选择后重试。"}), 400

    try:
        file_bytes = file_storage.read()
        rows = parse_history_csv(file_bytes)
        matched_row_count = count_history_rows(rows, start_time, end_time, view_type, selected_devices)
        if matched_row_count > HISTORY_ANALYSIS_MAX_ROWS:
            return jsonify({
                "ok": False,
                "message": f"当前选择的时间范围内日志记录条数过多，已超过上限（{HISTORY_ANALYSIS_MAX_ROWS} 行）。请缩小分析时间范围后重试。"
            }), 400
        series = build_history_series(rows, start_time, end_time, view_type, selected_devices)
    except ValueError as e:
        return jsonify({"ok": False, "message": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "message": f"历史分析失败：{e}"}), 500

    if not series:
        return jsonify({"ok": False, "message": "所选时间段内没有可用于绘图的数据。"}), 400

    return jsonify({
        "ok": True,
        "message": "",
        "view_type": view_type,
        "start_time": start_time.strftime(HISTORY_TIME_FORMAT),
        "end_time": end_time.strftime(HISTORY_TIME_FORMAT),
        "series": series,
    })


@socketio.on("connect")
def handle_connect():
    client_ip = request.headers.get("X-Forwarded-For", request.remote_addr)
    print(f"{time.strftime(HISTORY_TIME_FORMAT)} [INFO] client {client_ip} connected")
    with topology_lock:
        topology = copy.deepcopy(current_topology)
    emit("snapshot", topology)


@socketio.on("disconnect")
def handle_disconnect():
    client_ip = request.headers.get("X-Forwarded-For", request.remote_addr)
    print(f"{time.strftime(HISTORY_TIME_FORMAT)} [INFO] client {client_ip} disconnected")


def ensure_service_port_available(host, port):
    test_hosts = []
    host = (host or "").strip()
    if host in ("", "0.0.0.0"):
        test_hosts = ["0.0.0.0"]
    elif host == "::":
        test_hosts = ["::"]
    else:
        test_hosts = [host]
        
    last_error = None
    
    for bind_host in test_hosts:
        family = socket.AF_INET6 if ":" in bind_host else socket.AF_INET
        sock = socket.socket(family, socket.SOCK_STREAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((bind_host, port))
            return
        except OSError as e:
            last_error = e
        finally:
            sock.close()
    
    raise RuntimeError(
        f"程序启动失败：端口{port}已被使用，请先停止占用该端口的程序，或修改软件配置文件中的端口号之后重试。"
    ) from last_error


if __name__ == "__main__":
    try:
        ensure_deploy_ip_matches_local_host()
        ensure_service_port_available(SERVICE_HOST, SERVICE_PORT)
        init_latency_log()
        warm_up_cache()

        for ip in get_all_probe_ips():
            socketio.start_background_task(ping_worker, ip)

        socketio.start_background_task(publisher_loop)

        socketio.run(
            app,
            host=SERVICE_HOST,
            port=SERVICE_PORT,
        )
    except RuntimeError as e:
        print(f"{time.strftime(HISTORY_TIME_FORMAT)} [ERROR] {e}")
        raise SystemExit(1)
    except OSError as e:
        print(f"{time.strftime(HISTORY_TIME_FORMAT)} [ERROR] 程序启动失败：{e}")
        raise SystemExit(1)
