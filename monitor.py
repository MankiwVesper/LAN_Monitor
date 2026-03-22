import platform
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed


def ping_host(host, count=1, timeout=1000):
    system = platform.system()

    if system == "Windows":
        cmd = ["ping", "-n", str(count), "-w", str(timeout), host]
    else:
        timeout_sec = max(1, int(timeout / 1000))
        cmd = ["ping", "-n", "-c", str(count), "-W", str(timeout_sec), host]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True
        )
        output = (result.stdout or "") + "\n" + (result.stderr or "")
        latency = parse_ping_output(output)
        return latency
    except Exception:
        return None


def parse_ping_output(output):
    lines = output.splitlines()

    for line in lines:
        # Linux 常见格式:
        # rtt min/avg/max/mdev = 0.123/0.234/0.345/0.012 ms
        # 或
        # round-trip min/avg/max/stddev = ...
        if ("avg" in line or "round-trip" in line or "rtt" in line) and "=" in line and "/" in line:
            try:
                parts = line.split("=")[1].split("/")
                return float(parts[1].strip())
            except Exception:
                pass

        # 中文环境兜底
        if "平均" in line:
            try:
                avg = line.split("平均 =")[-1].replace("ms", "").strip()
                return float(avg)
            except Exception:
                pass

    return None


def ping_many(hosts, count=1, timeout=1000):
    if not hosts:
        return {}

    results = {}

    with ThreadPoolExecutor(max_workers=len(hosts)) as executor:
        futures = {
            executor.submit(ping_host, host, count, timeout): host
            for host in hosts
        }

        for future in as_completed(futures):
            host = futures[future]
            try:
                results[host] = future.result()
            except Exception:
                results[host] = None

    return results
