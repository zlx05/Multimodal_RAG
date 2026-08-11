"""pytest 全局配置：在收集测试前固定 JWT 相关环境变量，保证确定性测试密钥。

pytest 会先导入本文件再收集测试模块，因此在任何 `from ..core.config` import 之前
设置 env，`load_dotenv`（override=False）不会覆盖这里 setdefault 的值。
"""

import os

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-tests")
os.environ.setdefault("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "10080")
os.environ.setdefault("JWT_SETUP_TOKEN_EXPIRE_MINUTES", "15")
