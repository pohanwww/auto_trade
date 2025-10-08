from functools import partial, reduce


def compose(*functions):
    """函數組合"""
    return reduce(lambda f, g: lambda x: f(g(x)), functions, lambda x: x)


def pipe(data, *functions):
    """管道操作"""
    return reduce(lambda x, f: f(x), functions, data)


def curry(func):
    """柯里化函數"""

    def curried(*args, **kwargs):
        if len(args) + len(kwargs) >= func.__code__.co_argcount:
            return func(*args, **kwargs)
        return partial(func, *args, **kwargs)

    return curried


def map_data(func, data_list):
    """純函數映射"""
    return list(map(func, data_list))


def filter_data(predicate, data_list):
    """純函數篩選"""
    return list(filter(predicate, data_list))
