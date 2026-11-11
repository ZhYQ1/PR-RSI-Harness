"""演示用：包含高风险与普通代码的样例文件，用于验证 PR 风险审查工作流。

⚠️ 此文件仅用于测试风险审查能力，**不要**在真实项目中复用其中的写法。
"""

import hashlib
import os
import pickle
import subprocess

import requests

# 高风险样例 -----------------------------------------------------------------

def get_user(user_id: str):
    # sql_injection：字符串拼接构造 SQL
    query = "SELECT * FROM users WHERE id=" + user_id
    return query


def bootstrap():
    # hardcoded_secret：硬编码密钥
    api_key = "sk-live-1234567890abcdef"
    return api_key


def run_command(user_input: str):
    # command_injection：直接拼接用户输入到系统命令
    os.system("ls " + user_input)
    subprocess.run(f"echo {user_input}", shell=True)


def read_file(user_path: str):
    # path_traversal：拼接用户路径未做校验
    return open(base_dir + user_path).read()


def render_comment(element, comment: str):
    # xss：直接写入 HTML
    element.innerHTML = comment


def hash_password(password: str) -> str:
    # weak_crypto：使用 MD5
    return hashlib.md5(password.encode()).hexdigest()


def fetch_url():
    # ssrf：直接请求用户传入的 URL
    return requests.get(request.args["url"])


def load_config(data: bytes):
    # insecure_deserialization：反序列化不可信数据
    return pickle.loads(data)


# 普通（低风险）代码 -----------------------------------------------------------

def add(a: int, b: int) -> int:
    return a + b


def safe_query(user_id: str) -> str:
    # 参数化查询，不命中注入规则
    return "SELECT * FROM users WHERE id = %s", user_id


def safe_hash_password(password: str) -> str:
    # 使用强哈希
    return hashlib.sha256(password.encode()).hexdigest()


base_dir = "/var/app/data/"
