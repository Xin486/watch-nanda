"""Set the web operations password locally without command-line secrets."""
import getpass
from core.ops_auth import set_password

if __name__ == '__main__':
    try:
        first = getpass.getpass('运维口令（至少12字符）：')
        second = getpass.getpass('再次输入：')
        if first != second:
            raise ValueError('两次输入不一致')
        set_password(first)
        print('运维口令摘要已保存；不会自动修改操作系统 root 密码。')
    except (Exception, KeyboardInterrupt) as error:
        print('设置失败：' + type(error).__name__)
        raise SystemExit(1)
