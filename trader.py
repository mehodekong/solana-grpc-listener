import socket
import json
import requests
import os
import time
from datetime import datetime
from solders.keypair import Keypair
import base64
import base58
from solders.transaction import VersionedTransaction
from jito_py_rpc import JitoJsonRpcSDK

PARAM_FILE = "json_files/trade_params.json"
CONTROL_FILE = "json_files/control.json"
private_key_b58 = ""

def timestamp():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')

def load_trade_params():
    if not os.path.exists(PARAM_FILE):
        print(f"⚠️ 参数文件不存在: {PARAM_FILE}")
        return 1.0, 50
    with open(PARAM_FILE, "r") as f:
        params = json.load(f)
    sol_amount = params.get("sol_amount", 0.01)
    slippage = params.get("slippage", 15)
    jito_tip = params.get("jito_tip", 10000000)
    priority_fee = params.get("priority_fee", 10000000)
    return sol_amount, slippage, jito_tip, priority_fee

def load_status():
    if not os.path.exists(CONTROL_FILE):
        return "paused"
    with open(CONTROL_FILE, "r") as f:
        control = json.load(f)
    return control.get("trader", "paused")

def set_status(status):
    control = {}
    if os.path.exists(CONTROL_FILE):
        with open(CONTROL_FILE, "r") as f:
            control = json.load(f)
    control["trader"] = status
    with open(CONTROL_FILE, "w") as f:
        json.dump(control, f, indent=2)

def query_jupiter(token_mint, sol_amount, slippage):
    url = "https://quote-api.jup.ag/v6/quote"
    slippage_decimal = slippage / 100
    params = {
        "inputMint": "So11111111111111111111111111111111111111112",
        "outputMint": token_mint,
        "amount": int(sol_amount * 1e9),
        "slippageBps": int(slippage_decimal * 100),
        "swapMode": "ExactIn"
    }
    print(f"⏳ 查询中: {token_mint}")
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        swap_data = response.json()
        print(f"[{timestamp()}] ✅ 查询成功,进行构造交易")
        return swap_data
    except requests.RequestException as e:
        print(f"❌ 查询失败: {e}")
        return None

def execute_jupiter_swap(swap_data: dict, private_key_b58: str) -> str:
    try:
        keypair = Keypair.from_bytes(bytes(base58.b58decode(private_key_b58)))
        wallet_address = str(keypair.pubkey())

        # 构造交易（POST /v6/swap）
        swap_url = "https://quote-api.jup.ag/v6/swap"
        payload = {
            "userPublicKey": wallet_address,
            "wrapUnwrapSOL": True,
            "quoteResponse": swap_data
        }

        response = requests.post(swap_url, json=payload)
        response.raise_for_status()

        swap_tx_b64 = response.json().get("swapTransaction")
        if not swap_tx_b64:
            raise Exception("未返回 swapTransaction 字段")
        # 解码交易
        tx_bytes = base64.b64decode(swap_tx_b64)
        tx = VersionedTransaction.from_bytes(tx_bytes)
        # 签名交易
        signed_tx = VersionedTransaction(tx.message,[keypair])
        signed_tx_b64 = base64.b64encode(bytes(signed_tx)).decode("utf-8")
        print(f"[{timestamp()}] ✅ 已成功签名交易，将由jito提交")
        return signed_tx_b64
    except Exception as e:
        raise RuntimeError(f"发送交易失败: {e}")

def send_to_jito(signed_tx_b64):
    jito_rpc = JitoJsonRpcSDK(url="https://tokyo.mainnet.block-engine.jito.wtf/api/v1")
    response = jito_rpc.send_txn(
        params=signed_tx_b64,
        bundleOnly=None  # 如不设置为 True，则也可能提交至 RPC 主网
    )
    if response["success"]:
        print("✅ 交易提交成功：", response["data"])
    else:
        print("❌ 提交失败：", response["error"])

def start_socket_listener():
    set_status("running")
    host = "127.0.0.1"
    port = 56789
    sol_amount, slippage ,jito_tip, priority_fee = load_trade_params()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_socket:
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_socket.bind((host, port))
        server_socket.listen(5)

        previous_status = None

        print(f"[{timestamp()}] ✅ trader socket 已初始化，等待 monitor 连接...")

        while True:
            status = load_status()
            if status != previous_status:
                if status == "running":
                    print(f"[{timestamp()}] ✅ trader 已启动，监听代币地址...")
                elif status == "paused":
                    print(f"[{timestamp()}] ⏸️ 收到暂停信号，暂停监听...")
                previous_status = status

            # 阻塞等待连接，如果暂停则不监听
            if status != "running":
                time.sleep(1)
                continue

            try:
                conn, addr = server_socket.accept()  # 阻塞直到 monitor 发来连接
                with conn:
                    data = conn.recv(1024).decode().strip()
                    if not data:
                        continue
                    token_mint = data
                    print(f"\n[{timestamp()}] 接收到代币地址: {token_mint}")
                    print(f"🔧 当前参数 SOL: {sol_amount}, 滑点: {slippage}%, jito_tip: {jito_tip}, 优先费: {priority_fee}")
                    swap_data = query_jupiter(token_mint, sol_amount, slippage)
                    signed_tx_b64 = execute_jupiter_swap(swap_data, private_key_b58)
                    send_to_jito(signed_tx_b64)
            except Exception as e:
                print(f"⚠️ 处理过程中出错: {e}")

if __name__ == "__main__":
    try:
        start_socket_listener()
    except KeyboardInterrupt:
        print("❌ 已手动中断 trader")
        set_status("paused")
