import shioaji as sj


def create_api_client(api_key, secret_key, ca_path, ca_passwd, simulation=True):
    """純函數：建立API客戶端"""
    api = sj.Shioaji(simulation=simulation)
    api.login(api_key=api_key, secret_key=secret_key)
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
