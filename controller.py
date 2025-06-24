import subprocess
import psutil
import json
import os
import time

CONTROL_FILE = "json_files/control.json"
TRADE_PARAM_FILE = "json_files/trade_params.json"
CREATE_NEW_CONSOLE = 0x00000010

def init_control_file():
    os.makedirs(os.path.dirname(CONTROL_FILE), exist_ok=True)
    if not os.path.exists(CONTROL_FILE):
        with open(CONTROL_FILE, "w") as f:
            json.dump({"monitor": "stopped", "trader": "stopped"}, f)
    if not os.path.exists(TRADE_PARAM_FILE):
        with open(TRADE_PARAM_FILE, "w") as f:
            json.dump({"sol_amount": 0.01, "slippage": 15, "tip": 0.001, "priority_fee": 0.001}, f)

def write_control_status(program, state):
    try:
        with open(CONTROL_FILE, "r") as f:
            data = json.load(f)
    except:
        data = {}
    data[program] = state
    with open(CONTROL_FILE, "w") as f:
        json.dump(data, f)

def read_control_status(program):
    try:
        with open(CONTROL_FILE, "r") as f:
            data = json.load(f)
        return data.get(program, "stopped")
    except:
        return "stopped"

def is_program_running(script_name):
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            if "python" in proc.info['name'].lower() and proc.info['cmdline']:
                if any(script_name in s for s in proc.info['cmdline']):
                    return proc.pid
        except:
            continue
    return None

def start_program(label):
    if is_program_running(f"{label}.py"):
        if read_control_status(label) == "paused":
            write_control_status(label, "running")
            print(f"🔄 {label} 恢复运行。")
        else:
            print(f"⚠️ {label} 已在运行。")
        return
    subprocess.Popen(["python", f"{label}.py"], creationflags=CREATE_NEW_CONSOLE)
    write_control_status(label, "running")
    print(f"✅ 启动 {label}")

def pause_program(label):
    write_control_status(label, "paused")
    print(f"⏸️ 已暂停 {label}")

def stop_program(label):
    write_control_status(label, "stopped")
    pid = is_program_running(f"{label}.py")
    print(f"🛑 终止 {label}（如运行中将强制关闭）...")
    time.sleep(5)
    if pid:
        try:
            psutil.Process(pid).terminate()
            print(f"✅ {label} 已终止")
        except:
            pass

def save_trade_params(sol, slippage, tip, fee):
    params = {
        "sol_amount": sol,
        "slippage": slippage,
        "jito_tip": float(tip),
        "priority_fee": float(fee)
    }
    os.makedirs(os.path.dirname(TRADE_PARAM_FILE), exist_ok=True)
    with open(TRADE_PARAM_FILE, "w") as f:
        json.dump(params, f, indent=2)
    print("✅ 已保存参数：")
    print(json.dumps(params, indent=2))

def set_trader_params():
    try:
        sol = float(input("SOL 数量: "))
        slip = int(input("滑点(%): "))
        tip = float(input("Jito Tip(SOL): "))
        fee = float(input("优先费(SOL): "))
        save_trade_params(sol, slip, tip, fee)
        stop_program("trader")
        time.sleep(1)
        start_program("trader")
    except Exception as e:
        print(f"⚠️ 输入错误: {e}")

def update_status():
    status = {}
    for name in ["monitor", "trader"]:
        pid = is_program_running(f"{name}.py")
        state = read_control_status(name)
        if pid:
            if state == "paused":
                status[name] = "⏸️已暂停"
            elif state == "running":
                status[name] = "✅已启动"
            else:
                status[name] = "⚠️未知"
        else:
            status[name] = "🛑未运行"
    return status

def trader_menu():
    while True:
        status = update_status()
        print(f"\n[Trader] 状态:{status['trader']}")
        print("1. 启动 Trader\n2. 暂停 Trader\n3. 终止 Trader\n4. 设置交易参数\n5. 返回主菜单")
        c = input("输入指令: ").strip()
        if c == "1": start_program("trader")
        elif c == "2": pause_program("trader")
        elif c == "3": stop_program("trader")
        elif c == "4": set_trader_params()
        elif c == "5": break
        else: print("⚠️ 无效指令")
        time.sleep(1)

def monitor_menu():
    while True:
        status = update_status()
        print(f"\n[Monitor] 状态:{status['monitor']}")
        print("1. 启动 Monitor\n2. 暂停 Monitor\n3. 终止 Monitor\n4. 返回主菜单")
        c = input("输入指令: ").strip()
        if c == "1": start_program("monitor")
        elif c == "2": pause_program("monitor")
        elif c == "3": stop_program("monitor")
        elif c == "4": break
        else: print("⚠️ 无效指令")
        time.sleep(1)

def main():
    init_control_file()
    while True:
        status = update_status()
        print("\n===== 主菜单 =====")
        print(f"Monitor:{status['monitor']} | Trader:{status['trader']}")
        print("1. 控制 Monitor\n2. 控制 Trader\n3. 退出控制台")
        c = input("输入指令: ").strip()
        if c == "1": monitor_menu()
        elif c == "2": trader_menu()
        elif c == "3":
            stop_program("monitor")
            stop_program("trader")
            print("👋 已退出主控器。")
            break
        else:
            print("⚠️ 无效指令")
        time.sleep(1)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("⚠️ 中断退出")
