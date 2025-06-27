import grpc
from generated import geyser_pb2, geyser_pb2_grpc
import base58
from datetime import datetime
import time
import threading
import queue
import json
import os
import requests
from decimal import Decimal, ROUND_DOWN
import socket
import signal
import sys
import subprocess

wallets_file = "json_files/wallets-to-subscribe.json"
record_swap_file = "json_files/wallets-swap-record.json"
CONTROL_FILE = "json_files/control.json"
GRPC_SERVER = "solana-yellowstone-grpc.publicnode.com:443"
TELEGRAM_BOT_TOKEN = "7928616623:AAH-xJNy_jCiEVsA0H0IQNmDPzAse8dwyyk"
TELEGRAM_CHAT_ID = "7819265227"
sol_price_usd = 0.0
message_queue = queue.Queue()
PROJECT_DIR = os.path.abspath(os.path.dirname(__file__))
FILE_NAME = "wallets-swap-record.json"
RELATIVE_FILE_PATH = os.path.join("json_files", FILE_NAME)
FILE_PATH = os.path.join(PROJECT_DIR, RELATIVE_FILE_PATH)

def setup_git_user(name, email, global_config=False):
    if global_config:
        subprocess.run(["git", "config", "--global", "user.name", name], check=True)
        subprocess.run(["git", "config", "--global", "user.email", email], check=True)
    else:
        subprocess.run(["git", "config", "user.name", name], check=True)
        subprocess.run(["git", "config", "user.email", email], check=True)

def has_staged_changes():
    # 判断是否有待提交内容
    result = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=PROJECT_DIR)
    return result.returncode != 0

def upload_to_github():
    try:
        # 设置 Git 用户名邮箱（针对当前仓库）
        setup_git_user("mehodekong", "3304318271@qq.com", global_config=False)

        # 确保只跟踪指定文件
        subprocess.run(["git", "add", RELATIVE_FILE_PATH], cwd=PROJECT_DIR, check=True)

        if has_staged_changes():
            subprocess.run(["git", "commit", "-m", f"Auto upload {FILE_NAME}"], cwd=PROJECT_DIR, check=True)
            subprocess.run(["git", "push"], cwd=PROJECT_DIR, check=True)
            print(f"✅ {FILE_NAME} 上传成功")
            send_telegram_message(f"[{timestamp()}]\n✅ {FILE_NAME} 上传成功\n终止程序")
        else:
            print("📂 没有需要提交的改动，跳过提交")
            send_telegram_message(f"[{timestamp()}]\n📂 没有需要提交的改动，跳过提交,终止监控")

    except Exception as e:
        print(f"❌ 上传失败: {e}")
        send_telegram_message(f"[{timestamp()}]\n❌ 上传失败: {e}")


def to_subscript(n: str) -> str:
    subscript_map = {
        "3": "₃", "4": "₄", "5": "₅", "6": "₆"
    }
    return ''.join(subscript_map.get(c, c) for c in n)

def format_zero_subscript(token_price: float) -> str:
    d = Decimal(str(token_price)).quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)
    s = format(d, 'f')

    if not s.startswith("0."):
        return s

    decimal_part = s[2:]
    zero_count = 0
    for c in decimal_part:
        if c == '0':
            zero_count += 1
        else:
            break

    if zero_count < 3:
        return s

    # 尾数长度 = 8 - 零个数
    tail_len = 8 - zero_count
    tail = decimal_part[zero_count:zero_count + tail_len]

    zero_subscript = to_subscript(str(zero_count))

    return f"0.0{zero_subscript}{tail}"

def escape_markdown_v2(text: str) -> str:
    # 转义所有 MarkdownV2 特殊字符，包括 [] () 等
    escape_chars = '_*[]()~>#+=|{}.!-'
    trans_table = str.maketrans({c: '\\' + c for c in escape_chars})
    return text.translate(trans_table)


def send_telegram_message(message: str):
    while True:
        try:
            escaped_message = escape_markdown_v2(message)  # 👈 先转义
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            payload = {
                "chat_id": TELEGRAM_CHAT_ID,
                "text": escaped_message,
                "parse_mode": "MarkdownV2"
            }
            response = requests.post(url, data=payload)
            if response.status_code == 200:
                break
            else:
                print(f"{timestamp()}\nTelegram通知失败: {response.text}")
                print("重新发送")
        except Exception as e:
            print(f"{timestamp()}\n发送Telegram通知异常: {e}")
            print("重新发送")


def timestamp():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')

def update_sol_price():
    global sol_price_usd
    url = "https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd"

    while True:
        success = False
        while not success:
            try:
                response = requests.get(url, timeout=10)
                data = response.json()
                sol_price_usd = data['solana']['usd']
                success = True
            except Exception:
                time.sleep(1)  # 小延迟防止瞬间重试过快

        # 成功一次后再等待 300 秒
        time.sleep(300)

    # 提供外部函数获取当前价格
def get_sol_price():
    return sol_price_usd

def format_amount(amount: float) -> str:
    if amount >= 1_000_000:
        return f"{amount / 1_000_000:.2f}M"
    elif amount >= 1_000:
        return f"{amount / 1_000:.2f}K"
    else:
        return f"{amount:.2f}"  # 保留2位小数

def load_target_wallets():
    if os.path.exists(wallets_file):
        with open(wallets_file, "r") as f:
            return json.load(f)
    return []

def load_records():
    if os.path.exists(record_swap_file):
        with open(record_swap_file, "r") as f:
            return json.load(f)
    return {}

def save_records(records):
    with open(record_swap_file, "w") as f:
        json.dump(records, f, indent=2)

def update_wallet_record(records, wallet, token_address, buy_amount, buy_sol, sell_amount, sell_sol, current_amount):
    if wallet not in records:
        records[wallet] = {}
    if token_address not in records[wallet]:
        records[wallet][token_address] = {
            "amount": 0,
            "buy_count": 0,
            "buy_volume": 0,
            "sell_count": 0,
            "sell_volume": 0
        }
    token_data = records[wallet][token_address]
    token_data["amount"] = current_amount
    if buy_amount > 0:
        token_data["buy_count"] += 1
        token_data["buy_volume"] += buy_sol
    if sell_amount > 0:
        token_data["sell_count"] += 1
        token_data["sell_volume"] += sell_sol
    return records

def extract_target_signature(tx_info, target_wallets: set):
    try:
        signatures = tx_info.transaction.signatures
        account_keys = tx_info.transaction.message.account_keys
        num_signers = len(signatures)
        signer_keys = account_keys[:num_signers]

        for idx, key_bytes in enumerate(signer_keys):
            pubkey_str = base58.b58encode(key_bytes).decode()
            if pubkey_str in target_wallets:
                if idx < len(signatures):
                    return idx, pubkey_str, base58.b58encode(signatures[idx]).decode()
        return None, None, None
    except Exception as e:
        print(f"[{timestamp()}] 解析签名者异常: {e}")
        return None, None, None

def parse_token_transfers(tx_info, idx, wallet):
    try:
        pre_sol_balance = tx_info.meta.pre_balances[idx]
        post_sol_balance = tx_info.meta.post_balances[idx]
        pre_token_balances = tx_info.meta.pre_token_balances
        post_token_balances = tx_info.meta.post_token_balances

        wsol_mint = "So11111111111111111111111111111111111111112"
        sol_diff = abs(post_sol_balance - pre_sol_balance) / 1e9

        # 构建 pre/post token map
        pre_map = {t.mint: float(t.ui_token_amount.ui_amount) for t in pre_token_balances if t.owner == wallet}
        post_map = {t.mint: float(t.ui_token_amount.ui_amount) for t in post_token_balances if t.owner == wallet}

        # --- 先判断是否为“代币转出” ---
        for mint in set(pre_map) | set(post_map):
            pre_amount = pre_map.get(mint, 0)
            post_amount = post_map.get(mint, 0)
            if (
                pre_amount > post_amount  # 数量减少
                and wsol_mint not in pre_map
                and wsol_mint not in post_map
                and sol_diff < 0.00001  # 没花 SOL，仅 gas
            ):
                send_amount = pre_amount - post_amount
                current_amount = post_amount
                return mint, 0, 0, 0, send_amount, current_amount, False  # 立即返回转账信息

        # --- 否则开始判断是否买卖 ---
        token_address = None
        buy_amount = 0
        sell_amount = 0
        sol_amount = 0
        current_amount = 0
        first_buy = False

        for mint in set(pre_map) | set(post_map):
            pre_amount = pre_map.get(mint, 0)
            post_amount = post_map.get(mint, 0)
            delta = post_amount - pre_amount
            if mint == wsol_mint:
                sol_amount = abs(delta)
                print("交易使用 WSOL，结果可能存在错误")
                continue
            elif delta > 0:
                token_address = mint
                buy_amount = delta
                current_amount = post_amount
                if mint not in pre_map:  # 修复 first_buy 判定
                    first_buy = True
            elif delta < 0:
                token_address = mint
                sell_amount = -delta
                current_amount = post_amount

        # --- 正常买/卖行为时，计算 SOL ---
        if sol_amount == 0 and (buy_amount > 0 or sell_amount > 0):
            sol_amount = sol_diff

        return token_address, buy_amount, sell_amount, sol_amount, 0, current_amount, first_buy

    except Exception as e:
        print(f"[{timestamp()}] 解析转账异常: {e}")
        return None, 0, 0, 0, 0, 0, False


def process_messages(target_wallets):
    records = load_records()
    while True:
        response = message_queue.get()
        if response is None:
            break
        try:
            if response.WhichOneof("update_oneof") == "transaction":
                tx_info = response.transaction.transaction
                idx, wallet, signature = extract_target_signature(tx_info, target_wallets)
                if idx is None or signature is None:
                    continue

                print(f"[{timestamp()}] 发现交易")
                result = parse_token_transfers(tx_info, idx, wallet) or (None, 0, 0, 0, 0, 0, False)
                token_address, buy_amount, sell_amount, sol_amount, send_amount, current_amount, first_buy= result

                if token_address is None:
                    print(f"[{timestamp()}] 非代币交易，跳过\n")
                    continue
                old_buy_count = records.get(wallet, {}).get(token_address, {}).get("buy_count", 0)
                old_sell_count = records.get(wallet, {}).get(token_address, {}).get("sell_count", 0)
                sol_price = get_sol_price()
                if buy_amount > 0:
                    # send_token_to_trader(token_address)
                    token_price = (sol_amount * sol_price) / buy_amount
                    message = f"[{timestamp()}]\n钱包:{wallet}\nToken:`{token_address}`\n[买入]第{old_buy_count + 1}次\n数量:{format_amount(buy_amount)}\n余额:{format_amount(current_amount)}\n价格:${format_zero_subscript(token_price)}\n金额:{sol_amount:.2f} SOL\n"
                    if old_buy_count == 0 and first_buy:
                        message = f"🔔 首次[🟢买入]消息❗\n{message}"
                    print(message)
                    send_telegram_message(message)
                if sell_amount > 0 and old_buy_count != 0:
                    token_price = (sol_amount * sol_price) / sell_amount
                    message = f"[{timestamp()}]\n钱包:{wallet}\nToken:`{token_address}`\n[卖出]第{old_sell_count + 1}次\n数量:{format_amount(sell_amount)}\n余额:{format_amount(current_amount)}\n价格:${format_zero_subscript(token_price)}\n金额:{sol_amount:.2f} SOL\n"
                    if old_sell_count == 0:
                        message = f"🔔 首次[🔴卖出]消息❗\n{message}"
                    print(message)
                    send_telegram_message(message)
                if send_amount > 0:
                    message = f"[{timestamp()}]\n钱包:{wallet}\nToken:`{token_address}`\n[转出]\n数量:{format_amount(send_amount)}\n余额:{format_amount(current_amount)}\n"
                    print(message)
                    send_telegram_message(message)
                records = update_wallet_record(records, wallet, token_address, buy_amount, sol_amount if buy_amount > 0 else 0, sell_amount,sol_amount if sell_amount > 0 else 0, current_amount)
                save_records(records)

        except Exception as e:
            print(f"[{timestamp()}] 处理消息异常: {e}")

def run():
    First_start = True
    target_wallets = load_target_wallets()
    if not target_wallets:
        print(f"[{timestamp()}] 错误：未加载到任何监控钱包地址")
        return
    threading.Thread(target=process_messages, args=(target_wallets,), daemon=True).start()
    threading.Thread(target=update_sol_price, daemon=True).start()

    previous_state = None
    write_control_status("monitor", "running")

    while True:
        state = read_control_state()  # 读取 json 状态文件
        # 状态改变时打印提示
        if state != previous_state:
            if state == "running":
                print(f"[{timestamp()}] ✅ monitor 已启动，开始监听")
            elif state == "paused":
                print(f"[{timestamp()}] ⏸️ monitor 已暂停，监听挂起中")
            elif state == "stopped":
                print(f"[{timestamp()}] 🛑 已终止，程序退出中")
                break
            previous_state = state
        # 非运行状态则等待，不继续连接 gRPC
        if state != "running":
            time.sleep(1)
            continue

        print(f"[{timestamp()}] 正在连接 gRPC...")
        try:
            options = [
                ("grpc.keepalive_time_ms", 30000),  # 每 30 秒发送一次 keepalive
                ("grpc.keepalive_timeout_ms", 10000),  # 若 10 秒内未回复则认为断开
                ("grpc.keepalive_permit_without_calls", 1)  # 即使无活跃请求也发送
            ]
            channel = grpc.secure_channel(GRPC_SERVER, grpc.ssl_channel_credentials(),options)
            stub = geyser_pb2_grpc.GeyserStub(channel)

            request = geyser_pb2.SubscribeRequest(
                transactions={
                    "default": geyser_pb2.SubscribeRequestFilterTransactions(
                        account_include=target_wallets
                    )
                },
                commitment=geyser_pb2.CommitmentLevel.PROCESSED
            )
            print(f"[{timestamp()}] 已连接到 gRPC，正在监听 {len(target_wallets)} 个地址\n")
            if First_start:
                send_telegram_message(f"[{timestamp()}]\n✅已开始监控地址")
            if not First_start:
                print(f"[{timestamp()}]\n✅连接错误，已重启监控")
            for response in stub.Subscribe(iter([request])):
                # 循环中检测控制指令
                current_state = read_control_state()
                if current_state != "running":
                    print(f"[{timestamp()}] ⚠️ 接收到信号 {current_state}，中止监听")
                    break
                message_queue.put(response)

        except grpc.RpcError as e:
            error_str = str(e)
            if "getaddrinfo" in error_str or "WSA Error" in error_str or "DNS resolution failed" in error_str:
                print(f"[{timestamp()}] ❌ DNS解析失败，无法连接到结点地址")
            elif "no available node" in error_str:
                print(f"[{timestamp()}] ⚠️ 当前服务器无可用结点")
            elif "Connection timed out" in error_str:
                print(f"[{timestamp()}] ⚠️ 服务器未响应")
            elif "10054" in error_str or "Connection reset" in error_str:
                print(f"[{timestamp()}] ⚠️ 服务器主动断开连接")
            elif "RST_STREAM" in error_str and "error code 2" in error_str:
                print(f"[{timestamp()}] ⚠️ 服务器重置了流")
            else:
                print(f"[{timestamp()}] gRPC 连接异常: {e}")
            print(f"[{timestamp()}] 5秒后重连...\n")
            First_start = False
            time.sleep(5)
        except Exception as e:
            print(f"[{timestamp()}] 未知异常: {e}")
            print(f"[{timestamp()}] 5秒后重连...\n")
            First_start = False
            time.sleep(5)

def read_control_state():
    try:
        with open(CONTROL_FILE, "r") as f:
            return json.load(f).get("monitor", "running")
    except:
        return "running"

def write_control_status(program_name="monitor", state="running"):
    data = {}
    # 尝试读取已有控制文件内容
    if os.path.exists(CONTROL_FILE):
        try:
            with open(CONTROL_FILE, "r") as f:
                data = json.load(f)
        except json.JSONDecodeError:
            pass  # 文件为空或损坏时忽略，使用空字典
    # 更新状态
    data[program_name] = state
    # 写入新状态
    with open(CONTROL_FILE, "w") as f:
        json.dump(data, f)

def send_token_to_trader(token_mint):
    host = "127.0.0.1"
    port = 56789
    try:
        with socket.create_connection((host, port), timeout=3) as s:
            s.sendall(token_mint.encode())
            print(f"✅ 已发送代币地址到 trader: {token_mint}")
    except Exception as e:
        print(f"❌ 无法发送代币地址: {e}")

def graceful_exit(*args):
    print("程序即将退出，开始上传最新文件")
    upload_to_github()
    message_queue.put(None)
    print("程序已退出")
    sys.exit(0)

if __name__ == "__main__":
    # 监听 SIGINT（Ctrl+C）和 SIGTERM（systemd 停止）
    signal.signal(signal.SIGINT, graceful_exit)
    signal.signal(signal.SIGTERM, graceful_exit)
    #主程序
    run()