"""routers/ - 阶段5③拆分的 FastAPI APIRouter 集合（PHASE_5 §5.2）。

各 router 只做参数校验和调用 service，不含业务逻辑；共享状态走 deps。
api_server.py 通过 app.include_router(...) 挂载。
"""
