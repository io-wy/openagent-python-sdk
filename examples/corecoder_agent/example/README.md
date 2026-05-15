# Shop Backend Example (FastAPI)

这个目录提供一个完整度更高的 Shop 后端示例：

- 用户与角色（customer/admin）
- 分类与商品管理
- 地址管理
- 购物车
- 订单流程（created -> paid -> shipped -> completed）
- 订单取消并回滚库存

## 文件结构

- `shop_api.py`：主 API
- `seed.py`：示例数据初始化脚本
- `test_shop_api.py`：接口测试

## 启动

在仓库根目录执行：

```bash
uvicorn examples.corecoder_agent.example.shop_api:app --reload
```

访问：

- Swagger UI: `http://127.0.0.1:8000/docs`
- Health: `http://127.0.0.1:8000/health`

## 初始化示例数据

```bash
python -m examples.corecoder_agent.example.seed
```

脚本会在内存中创建：

- 1 个管理员
- 1 个普通用户
- 2 个分类
- 3 个商品
- 1 个地址

并输出可用于请求头的 token。

## 测试

```bash
pytest -q examples/corecoder_agent/example/test_shop_api.py
```

## 鉴权说明

除了公开接口（如 health、商品列表）外，其它接口需要请求头：

```text
X-Token: token-xxx
```

可通过 `POST /users` 注册用户拿到 token，或使用 `seed.py` 输出的 token。
