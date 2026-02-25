"""API client management."""

import time

import shioaji as sj


def create_api_client(
    api_key, secret_key, ca_path=None, ca_passwd=None, simulation=True,
    max_retries: int = 3, retry_delay: int = 10,
):
    """建立 API 客戶端（登入失敗自動重試）"""
    api = sj.Shioaji(simulation=simulation)

    for attempt in range(1, max_retries + 1):
        try:
            api.login(api_key=api_key, secret_key=secret_key)
            break
        except Exception as e:
            print(f"⚠️  登入失敗 (第 {attempt}/{max_retries} 次): {type(e).__name__}: {e}")
            if attempt == max_retries:
                raise
            print(f"⏳ {retry_delay} 秒後重試...")
            time.sleep(retry_delay)

    if ca_path and ca_passwd:
        api.activate_ca(
            ca_path=ca_path,
            ca_passwd=ca_passwd,
        )
    return api


def with_api_client(api_func):
    """高階函數：將API客戶端注入到函數中"""

    def wrapper(api_client, *args, **kwargs):
        return api_func(api_client, *args, **kwargs)

    return wrapper
