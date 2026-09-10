# Mini LLM Gateway

把 `course_code/week01/1-6/gateway.py` 单文件里验证过的模型网关**核心语义**，工程化为一个
**可配置、可部署、可治理**的服务。设计规格见 [`design_spec.md`](./design_spec.md)（`§` 小节编号与代码一一对应）。

## 分层结构（换供应商不动业务，换协议不动适配）

```
mini_llm_gateway/
  protocol/     # §1.2 内部协议：Message / LLMRequest / LLMResponse / StreamEvent / GatewayError
  adapters/     # §2   供应商适配：ProviderAdapter / FakeAdapter / OpenAICompatibleAdapter(httpx)
  routing/      # §5   RoutePolicy + 重试 + Gateway 执行器（complete / stream / 取消）
  structured/   # §4   出口 JSON Schema 校验
  prompts/      # §6   Prompt Bundle：可版本化、可加载、可校验的受控模板
  governance/   # §7   审计 TraceSink / 配额限流预算 / 指标
  config/       # §10  env + yaml 分层配置（models.yaml）
  app.py        # §9   FastAPI 装配（依赖注入 http_client / adapter_override）
config/models.yaml          # 模型白名单、价格、能力
prompts/bundles/<name>/<version>.yaml
tests/                      # 全离线：ASGITransport + MockTransport / FakeAdapter
```

## 安装

```bash
cd course_task/mini-llm-gateway
pip install -e ".[dev]"
```

## 运行测试（全离线，无网络/无密钥/无抖动）

```bash
pytest            # 或 make test
```

测试用两种手法（§11.1）：
- **手法 B（默认，端到端）**：外层 `ASGITransport` 打进 app，内层 `MockTransport` 冒充上游 HTTP，
  网关真实的路由/结构化/SSE 代码全跑，只有最底层网络被顶替。
- **手法 A（协程级时序/协议单测）**：直接注入 `FakeAdapter`（`adapter_override`）。

## 静态检查

```bash
make lint    # ruff check .
make type    # mypy mini_llm_gateway
```

## 启动服务

```bash
cp .env.example .env      # 填入 GATEWAY_AUTH_TOKEN 与供应商密钥
make run                  # uvicorn --factory，--reload
```

> 用 `--factory build_default_app` 启动：模块级不构造 app，避免导入期读配置/建连接。
> 未配置 `GATEWAY_AUTH_TOKEN` 时以**无鉴权模式**运行并在启动日志告警（§7.3）。

## Docker

```bash
make docker               # docker build -t mini-llm-gateway:0.1.0 .
docker compose up         # 注入 .env，映射 8000，挂载 config/ 与 prompts/
```

`docker run` 后 `GET /healthz` 返回 200 即算可启动。

> **podman 用户**：命令兼容，`podman build -t mini-llm-gateway:0.1.0 .` 即可。
> 镜像内的 `HEALTHCHECK` 在 podman 默认 OCI 格式下会被忽略（可用 `--format docker` 构建以保留）；
> `docker-compose.yml` 已额外提供 compose 层健康检查，docker / podman 均生效。

## API（§9）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/v1/llm` | 非流式统一入口，出口 `LLMResponse` 校验 |
| POST | `/v1/llm/stream` | 流式 SSE；响应头带 `X-Request-Id`；禁止 `response_schema` |
| POST | `/v1/llm/stream/{request_id}/cancel` | 显式取消（202） |
| GET | `/v1/traces` | 审计查询（不含模型回答文本） |
| GET | `/healthz` | 存活/就绪探针 |
| GET | `/metrics` | Prometheus 文本指标 |

### 非流式

```bash
curl http://127.0.0.1:8000/v1/llm \
  -H 'authorization: Bearer <TOKEN>' -H 'content-type: application/json' \
  -d '{"model":"general-primary","messages":[{"role":"user","content":"解释什么是 LLM Gateway"}]}'
```

### Structured Output

```bash
curl http://127.0.0.1:8000/v1/llm \
  -H 'authorization: Bearer <TOKEN>' -H 'content-type: application/json' \
  -d '{"model":"general-primary","messages":[{"role":"user","content":"返回一个答案"}],
       "response_schema":{"type":"object","properties":{"answer":{"type":"string"}},
       "required":["answer"],"additionalProperties":false}}'
```

### Streaming

```bash
curl -N http://127.0.0.1:8000/v1/llm/stream \
  -H 'authorization: Bearer <TOKEN>' -H 'content-type: application/json' \
  -d '{"model":"general-primary","messages":[{"role":"user","content":"用一句话解释流式输出"}]}'
```

SSE 事件为 `content.delta` / `response.completed` / `response.failed`。首块后不切换备用模型。

### Prompt 模板

```json
{"model":"general-primary","messages":[{"role":"user","content":"..."}],
 "prompt":{"name":"knowledge_decision","version":"v1","variables":{"product_name":"差旅助手"}}}
```

## 验收标准映射

| 验收项 | 对应 |
|---|---|
| 测试全绿 | `pytest`（全离线，FakeAdapter + MockTransport） |
| 静态检查通过 | `ruff check .` + `mypy mini_llm_gateway` |
| Docker 可启动 | `docker build` + `/healthz` 200 |
| API 可调用 | `/v1/llm`、`/v1/llm/stream`、`/v1/traces` 按 §9 契约响应 |

## 接入真实模型

`config/models.yaml` 通过 `${VAR:default}` 从环境读取供应商模型名/地址，密钥经 `api_key_env`
只由网关进程读取；调用方无需持有供应商密钥。真实集成测试见 `tests/test_integration_real.py`
（需 `DEEPSEEK_API_KEY`，默认自动跳过）。
