# Mini LLM Gateway 设计规格（Design Spec）

> 把 `course_code/week01/1-6/gateway.py` 单文件里验证过的模型网关**核心语义**，
> 迁移到一个**可配置、可部署、可治理**的服务中——即进一步工程化。
>
> 本文是**设计规格**，不是实现。所有实现须回指本文的 `§` 小节编号，
> 与 `mini_code_agent`（`code_change_planner_spec.md`）保持同样的规格约定。

- 状态：Draft v0.1
- 源语义基线：`course_code/week01/1-6/gateway.py` + `gateway_summary.md` + `test_gateway.py` / `test_gateway2.py`
- 目标产物：`course_task/mini-llm-gateway/`（Python 包 + FastAPI 服务 + Docker + 测试）

---

## §0 目标与非目标

### 0.1 要迁移的核心语义（来自单文件基线，必须等价保留）

| 单文件里的语义 | 出处（gateway.py） | 本文归属 |
|---|---|---|
| 统一请求/响应协议，`extra="forbid"` 拒绝未知字段 | `LLMRequest` / `LLMResponse` | §1 |
| 平台模型名 → 供应商配置映射（白名单） | `MODEL_CONFIGS` / `validate_model` | §1 §5 |
| Provider 抽象（`complete` / `stream`），业务不依赖 SDK | `Provider` Protocol | §1 §2 |
| OpenAI Compatible 适配 + 密钥留在网关内 | `OpenAICompatibleProvider` | §2 |
| Streaming 走 SSE，首块前可切备用、首块后只发流内错误 | `stream_with_fallback` | §3 |
| Structured Output：`json_schema` / `json_object` 双模式 + 出口 `jsonschema` 校验 | `complete` / `call_with_fallback` | §4 |
| 有限重试 + 能力等价 fallback（不等价则拒绝） | `call_with_fallback` / `is_retryable` | §5 |
| 受控 Prompt 模板，调用方只传 name+version+variables | `PROMPT_TEMPLATES` / `render_prompt` | §6 |
| 审计追踪：记录用量/成本/延迟/模型/模板版本，**不记文本** | `CallTrace` / `record_trace` | §7 |
| 稳定错误码 + HTTP 状态映射 | `GatewayError` | §8 |

### 0.2 工程化新增目标（本次要补齐的部分）

1. **§1 跑通内部协议与 Fake Adapter**：协议层与适配层解耦，`FakeAdapter` 支撑全离线测试。
2. **§2 接入真实 Adapter**：OpenAI Compatible / DeepSeek 真实适配，凭据只由网关持有。
3. **§3 Streaming 与取消**：SSE 流式 + 客户端断连 / 超时 / 显式取消的协同取消（源文件缺失）。
4. **§4 Structured Output**：双模式 + 出口 Schema 校验 + 失败可定位。
5. **§5 模型路由与 Fallback**：可配置路由策略 + 能力等价 fallback + 重试停止条件。
6. **§6 Prompt Bundle**：Prompt 从硬编码 dict 升级为**可版本化、可加载、可校验**的 Bundle 资产。
7. **§7 资源治理与观测闭环**：配额/限流/预算 + 结构化日志 + 指标 + 审计查询的闭环。

### 0.3 非目标

- 不做多租户计费结算系统（只做用量与成本**统计**，见 §7）。
- 不做前端 UI；只交付 HTTP API。
- 不实现向量检索 / RAG / Agent 循环（那是其它课程任务的范畴）。
- 不引入分布式持久化存储；审计与配额默认内存 + 可插拔后端接口（见 §7.4）。

---

## §1 内部协议与 Fake Adapter

### 1.1 分层架构（换供应商不动业务，换协议不动适配）

```
mini_llm_gateway/
  protocol/        # 内部协议（与供应商、与传输无关）
    messages.py        # Message / Role
    request.py         # LLMRequest（extra=forbid，组合校验）
    response.py        # LLMResponse / Usage
    events.py          # StreamEvent：content.delta / response.completed / response.failed
    errors.py          # GatewayError / ErrorCode（§8）
  adapters/        # 供应商适配层（吸收 SDK 差异）
    base.py            # ProviderAdapter 抽象：complete() / stream()
    capabilities.py    # ProviderCapabilities（结构化能力、流式、上下文窗口）
    openai_compatible.py   # §2 真实适配
    fake.py            # §1 FakeAdapter（脚本化，可注入失败/延迟/分块）
  routing/         # §5 路由与 fallback
    policy.py          # RoutePolicy：主/备序列、能力等价校验
    executor.py        # call_with_fallback / stream_with_fallback + 取消（§3）
    retry.py           # 单次调用层停止条件（max_attempts / retryable）
  prompts/         # §6 Prompt Bundle
    bundle.py          # PromptBundle / PromptTemplate 加载与校验
    store.py           # 文件系统/内存 loader
  governance/      # §7 治理与观测
    quota.py           # 配额/限流/预算
    tracing.py         # CallTrace / TraceSink
    metrics.py         # 指标导出
  structured/      # §4 结构化输出
    validate.py        # 出口 jsonschema 校验 + 错误归一化
  config/
    settings.py        # 分层配置：env + yaml（pydantic-settings）
    models.yaml        # 模型白名单与能力（可配置，替代 MODEL_CONFIGS）
  app.py           # FastAPI 装配（依赖注入 adapter/router/store/governance/http_client）
  __init__.py
tests/
  ...              # 全离线：外层 ASGITransport + 内层 MockTransport(伪上游)，见 §11.1
```

> **可测性要求**：`create_app(config, http_client=...)` 必须允许注入上游 `http_client`，
> 以便测试用 `httpx.MockTransport` 顶替最底层网络传输（§11.1.2）。

### 1.2 内部协议（等价迁移，字段冻结）

- `Message`：`role ∈ {system,user,assistant}`，`content` 长度 `[1, 20000]`。
- `LLMRequest`：`model / messages / stream / response_schema / timeout_seconds(0,120] / prompt`。
  - `extra="forbid"`（未知字段 → 422）。
  - 组合校验：`stream=True` 且 `response_schema` 非空 → 拒绝（§4.3）。
- `LLMResponse`：`request_id / model(实际模型) / content / parsed / usage / latency_ms / attempts`。
- `StreamEvent`：`content.delta{delta}` / `response.completed{model}` / `response.failed{error}`。

> **不变量**：协议层不 import 任何供应商 SDK。适配层不 import FastAPI。
> 这是"换模型不动循环 / 换协议不动适配"的结构保证（与 `mini_code_agent` 同构）。

### 1.3 Fake Adapter（离线测试基石）

`FakeAdapter(ProviderAdapter)` 需支持脚本化控制：

- `content` / 结构化 `content`（JSON 字符串）；
- `failures=n`：前 n 次调用抛可重试异常（对齐源 `FakeProvider(failures=2)`）；
- `chunks=[...]`：`stream()` 逐块产出；
- `delay` / `cancel_after`：为 §3 取消测试注入可控时序；
- 记录 `complete_calls` / `stream_calls` 以断言路由与 fallback 行为。

**验收（§1）**：单元/契约测试无网络、无真实密钥、无抖动即可跑通——协程级时序与
协议分支用 `FakeAdapter`（手法 A），端到端链路用 `MockTransport` 伪上游（手法 B，§11.1）。

---

## §2 接入真实 Adapter

### 2.1 ProviderAdapter 抽象

```python
class ProviderAdapter(ABC):
    name: str
    capabilities: ProviderCapabilities
    async def complete(self, spec: ProviderCallSpec) -> tuple[str, Usage]: ...
    async def stream(self, spec: ProviderCallSpec) -> AsyncIterator[str]: ...
```

`ProviderCallSpec` 携带：解析后的供应商模型名、messages、timeout、response_schema、
结构化模式（`json_schema` / `json_object` / `prompt_only`）。适配层负责把内部协议翻译为
具体 SDK 调用，**并把 SDK 异常归一化为可重试/不可重试语义（§8）**。

### 2.2 OpenAICompatibleAdapter（真实）

- 从 `ProviderCallSpec` 组装 `chat.completions.create`；
- **密钥治理**：`api_key` 从 `config.api_key_env` 读取，仅网关进程持有；
  缺失 → `GatewayError("gateway_misconfigured", 503)`（对齐源语义）。
- `max_retries=0`：重试完全由 §5 的 `routing/retry.py` 掌控，不让 SDK 隐式重试。
- 结构化：`json_schema` 模式透传 schema；`json_object` 模式把 schema 注入 system message
  （等价迁移源 `OpenAICompatibleProvider.complete`）。

### 2.3 可配置模型白名单（替代 `MODEL_CONFIGS` 硬编码）

`config/models.yaml`（可被 env 覆盖）：

```yaml
models:
  general-primary:
    provider: openai_compatible
    provider_model: ${PRIMARY_PROVIDER_MODEL:deepseek-chat}
    base_url: ${PRIMARY_BASE_URL:https://api.deepseek.com}
    api_key_env: DEEPSEEK_API_KEY
    supports_structured_output: true
    structured_output_mode: json_object
    context_window: 65536
    price_per_million: { input: 1.0, output: 4.0 }
  general-backup:
    provider: openai_compatible
    provider_model: ${BACKUP_PROVIDER_MODEL:deepseek-chat}
    base_url: ${BACKUP_BASE_URL:https://api.deepseek.com}
    api_key_env: DEEPSEEK_BACKUP_API_KEY
    supports_structured_output: true
    structured_output_mode: json_object
    context_window: 65536
    price_per_million: { input: 0.8, output: 3.2 }
```

**验收（§2）**：`test_gateway2.py` 等价的真实集成脚本可选执行（有 `DEEPSEEK_API_KEY` 时），
无密钥时自动跳过；离线路径不受影响。

---

## §3 Streaming 与取消

### 3.1 SSE 语义（等价迁移）

- 独立入口 `POST /v1/llm/stream`，`media_type=text/event-stream`；
- 事件：`content.delta` → … → `response.completed{model}`，失败发 `response.failed{error}`；
- **首块前**上游失败且可重试 → 可切备用模型；**首块后**失败只发流内 `response.failed`，
  绝不重发已产出的文本（对齐源 `stream_with_fallback` 的 `emitted` 语义）。

### 3.2 取消（源文件缺失，本次补齐）

取消有三个触发源，需统一收敛：

1. **客户端断连**：SSE 生成器在 `yield` 时感知 `request.is_disconnected()` / `asyncio.CancelledError`；
2. **超时**：整体墙钟超时（`timeout_seconds`）用 `asyncio.timeout()` 包裹上游流；
3. **显式取消**：可选 `POST /v1/llm/stream/{request_id}/cancel` 设置取消标志。

取消处理约定：

- 捕获取消 → **立即停止向上游拉取**（关闭上游 response / 退出 `async for`）；
- 记一条 `status="cancelled"` 的 `CallTrace`（§7），带已产出 token 的**近似**用量；
- 非流式 `complete()` 同样受 `timeout_seconds` 约束，取消即释放上游连接，不泄漏协程。

> **不变量**：任何取消路径都不得留下悬挂的上游连接或未 `record_trace` 的调用。

**验收（§3）**：FakeAdapter 注入 `chunks + cancel_after`，断言：
(a) 客户端断连后上游停止被拉取；(b) 已产出内容不重复；(c) 产生 `cancelled` 审计记录。

---

## §4 Structured Output

### 4.1 双模式（等价迁移）

- `json_schema`：`response_format={"type":"json_schema", "json_schema":{strict,schema}}`；
- `json_object`：`response_format={"type":"json_object"}` + 把 schema 注入 system message。
- 模式由模型能力 `structured_output_mode` 决定（§2.3）。

### 4.2 出口校验与错误归一化

- 收到 content 后 `json.loads` → `jsonschema.validate`；
- `json.loads` 失败 → `GatewayError("invalid_json", 502)`；
- schema 不符 → `GatewayError("schema_validation_failed", 502)`；
- 成功则在 `LLMResponse.parsed` 返回已解析对象（对齐源与 `test_gateway`）。

### 4.3 组合约束

- 请求层：`stream + response_schema` → 422（协议 `model_validator`）；
- 流式入口若带 `response_schema` → 400 `unsupported_combination`；
- 请求模型不支持结构化 → 400 `structured_output_unsupported`（在路由校验时拦截，§5.2）。

**验收（§4）**：非法 JSON → 502 `invalid_json`；合法 JSON → 200 且 `parsed` 正确
（对齐 `test_gateway.py` 两个结构化用例）。

---

## §5 模型路由与 Fallback

### 5.1 路由策略（可配置）

`RoutePolicy` 从配置读取每个逻辑模型的**候选序列**（默认 `[requested_model, general-backup]`，
用 `dict.fromkeys` 去重保序，等价迁移）。策略可扩展为按标签/成本/优先级选择，但默认行为
与单文件一致。

### 5.2 能力等价校验（防止"降级到不等价模型"）

fallback 前对候选模型做 `validate_model`：

- 模型不在白名单 → `unknown_model`（若是**主模型**直接抛，若是备用则跳过该候选）；
- 请求要结构化但候选不支持 → `structured_output_unsupported`。

> **不变量**：结构化请求只能 fallback 到同样支持结构化输出的模型，不做静默降级。

### 5.3 两层停止条件（与 `mini_code_agent` 同款约定，勿混淆）

- **单次调用层**（`retry.py`）：每个模型最多尝试 2 次；仅当异常
  `isinstance(APIConnectionError|APITimeoutError|RateLimitError|TimeoutError|ConnectionError)`
  才重试；不可重试异常立即 break 到下一模型或抛出。
- **路由层**（`executor.py`）：遍历候选模型序列；全部失败 → `model_unavailable`（502）。

`GatewayError` 在重试/路由过程中**直接透传**，不被当作可重试上游错误（等价迁移）。

### 5.4 响应与审计

- `LLMResponse.model` 返回**实际**使用的模型（可能是 backup）；
- `attempts` 累计所有模型的总尝试次数（主重试 1 次后切备用 → `attempts=3`，对齐测试）。

**验收（§5）**：`FakeAdapter(failures=2)` → 200 且 `model=general-backup`、`attempts=3`
（对齐 `test_gateway.py` retry 用例与 `test_gateway2.py` fallback 用例）。

---

## §6 Prompt Bundle

### 6.1 从硬编码 dict 升级为可版本化资产

源里 `PROMPT_TEMPLATES` 是模块级 dict。工程化后升级为 **Prompt Bundle**：

- 目录 `prompts/bundles/<name>/<version>.yaml`，每个文件是一个模板资产：

```yaml
name: knowledge_decision
version: v1
system_template: |
  你是${product_name}的知识库决策器。资料不足时搜索，资料充分时结束回答。不得编造制度内容。
required_variables: [product_name]
```

- `PromptStore` 启动时加载全部 bundle，做**校验**：模板可解析、`required_variables` 与
  `${...}` 占位符一致；重复 `(name,version)` 报错。

### 6.2 渲染与治理约束（等价迁移 + 强化）

- 调用方只能传 `PromptSelection{name, version, variables}`，**不能提交或覆盖模板正文**；
- 渲染用 `string.Template.substitute`：
  - 未知 `(name,version)` → 400 `unknown_prompt_template`；
  - 缺变量 → 400 `missing_prompt_variable`（对齐 `test_gateway2` 用例）；
- 渲染出的 system message 前置注入到 `messages`（`build_messages` 语义）。

**验收（§6）**：缺变量 → 400 `missing_prompt_variable`；未知模板 → 400 `unknown_prompt_template`；
带 prompt 的正常请求成功且审计记录到 `prompt_name/prompt_version`。

---

## §7 资源治理与观测闭环

### 7.1 审计追踪（等价迁移 + 抽象化）

`CallTrace` 字段与源一致：`request_id / timestamp / requested_model / actual_model /
prompt_name / prompt_version / input_tokens / output_tokens / cost_usd / latency_ms /
attempts / status / error_code`。`status` 扩展为 `success | failed | cancelled`（§3）。

- **默认不记录 messages / content / parsed**（隐私红线，对齐源与测试
  `all("content" not in item ...)`）。
- 成本 `cost_usd` 用 `price_per_million`（§2.3）按实际模型计算。

### 7.2 TraceSink（可插拔）

抽象 `TraceSink.emit(trace)`：默认 `InMemoryTraceSink`（支撑 `GET /v1/traces`），
可替换为文件/stdout-json/外部后端，接口不变。

### 7.3 配额 / 限流 / 预算

`governance/quota.py` 提供请求前置检查（默认内存实现，可关闭）：

- **并发/速率限流**：按 API Key 或全局的 RPS / 并发上限；超限 → 429 `rate_limited`；
- **成本预算**：窗口内累计 `cost_usd` 超阈值 → 429 `budget_exceeded`；
- **请求上限**：单请求 `timeout_seconds ≤ 120`、messages 数量与长度上限（协议层已约束）。

> **安全提示**：网关是对外网络端点。默认应启用**鉴权**（API Key header），
> 未配置鉴权时启动日志必须显式告警"运行在无鉴权模式"。鉴权中间件为可插拔项。

### 7.4 观测指标

`governance/metrics.py` 暴露 `GET /metrics`（Prometheus 文本格式，可选）：

- `llm_requests_total{model,status}`、`llm_latency_ms`（直方图）、
  `llm_tokens_total{direction}`、`llm_cost_usd_total{model}`、`llm_fallback_total`。
- `GET /healthz`：存活/就绪探针（供 Docker / 编排使用）。

### 7.5 观测闭环

请求 → 限流/预算检查 → 路由执行 → `record_trace` + 指标递增 → `/v1/traces` 与 `/metrics`
可查。失败与取消同样进入闭环，保证"每次调用都有一条可审计记录"。

**验收（§7）**：审计不含文本；成功/失败/取消均产生记录；`cost_usd/latency_ms ≥ 0`；
超预算/超限触发 429（离线用 FakeAdapter + 造数据断言）。

---

## §8 错误分类（Error Taxonomy）

`GatewayError(code, message, status_code)` → HTTP `{detail:{code,message}}`。稳定错误码：

| code | HTTP | 触发 | 归属 |
|---|---|---|---|
| （FastAPI 校验）| 422 | 未知字段 / 非法组合 / 类型错误 | §1 §4.3 |
| `unknown_model` | 400 | 模型不在白名单 | §5.2 |
| `structured_output_unsupported` | 400 | 模型不支持结构化 | §4.3 §5.2 |
| `unknown_prompt_template` | 400 | 模板不存在 | §6 |
| `missing_prompt_variable` | 400 | 缺 Prompt 变量 | §6 |
| `unsupported_combination` | 400 | 流式带 response_schema | §4.3 |
| `use_stream_endpoint` | 400 | 非流式入口收到 `stream=true` | §3 |
| `invalid_json` | 502 | 结构化返回非法 JSON | §4.2 |
| `schema_validation_failed` | 502 | 结构化不符 Schema | §4.2 |
| `gateway_misconfigured` | 503 | 模型凭据缺失 | §2.2 |
| `model_unavailable` | 502 | 主备模型全部失败 | §5.3 |
| `upstream_stream_failed` | (流内) | 流式上游失败 | §3 |
| `rate_limited` / `budget_exceeded` | 429 | 触发治理阈值 | §7.3 |

> **不变量**：暴露给调用方的错误只有稳定 code + 安全 message，绝不透传上游 SDK 的原始堆栈或密钥。

---

## §9 API 契约

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/v1/llm` | 非流式统一入口，`response_model=LLMResponse`（出口校验）|
| POST | `/v1/llm/stream` | 流式 SSE 入口，禁止 `response_schema` |
| POST | `/v1/llm/stream/{request_id}/cancel` | 显式取消（§3，可选）|
| GET | `/v1/traces` | 审计查询（`response_model=list[CallTrace]`）|
| GET | `/healthz` | 存活/就绪探针 |
| GET | `/metrics` | Prometheus 指标（可选）|

请求/响应字段与 §1 一致；FastAPI 在入口校验请求、在 `response_model` 校验出口。

---

## §10 配置与部署

### 10.1 分层配置

- **env（凭据与开关）**：`DEEPSEEK_API_KEY` / `DEEPSEEK_BACKUP_API_KEY` /
  `PRIMARY_*` / `BACKUP_*` / `GATEWAY_AUTH_TOKEN` / `ENABLE_METRICS` / `PROMPTS_DIR`。
- **yaml（模型白名单、价格、能力）**：`config/models.yaml`（§2.3），env 可插值覆盖。
- 用 `pydantic-settings` 加载并校验；缺关键凭据时启动即报错（fail-fast）。
- 交付 `.env.example`（对齐 `mini_code_agent` 约定）。

### 10.2 Docker（验收：可启动）

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
RUN pip install --no-cache-dir .
COPY . .
EXPOSE 8000
# 非 root 运行；HEALTHCHECK 打 /healthz
HEALTHCHECK CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/healthz')"
CMD ["uvicorn", "mini_llm_gateway.app:app", "--host", "0.0.0.0", "--port", "8000"]
```

- 提供 `docker-compose.yml`：注入 env、映射 8000、挂载 `prompts/`；
- `docker build` 成功且 `docker run` 后 `/healthz` 返回 200 即算"可启动"。

---

## §11 测试策略与验收标准

### 11.1 全离线测试（无网络、无密钥、无抖动）

全部离线，无网络、无真实密钥、无 sleep 抖动（重试 `base_delay=0`）。测试在**两个层次**
切断真实链路，按测试目的选用；真实集成（DeepSeek）作为**可选**脚本，有密钥才跑。

#### 11.1.1 两层拦截：在哪一层放"假货"

一次请求在网关内经过的层次（从外到内）：

```
① HTTP 入口(FastAPI 路由) → ② 鉴权/限流 → ③ 路由/重试/fallback
  → ④ 结构化校验/prompt 注入/组装上游请求体 → ⑤ Provider 把请求体拼成 HTTP 发出
  ────────────────────────────────────────────────  🌐 真实供应商
```

离线的本质是"不让 ⑤ 真的把包发到互联网"，但可以选在两个深度切断：

| 手法 | 切断点 | 被真实执行的代码 | 适用 |
|---|---|---|---|
| **A. FakeAdapter** | ④/⑤ 之间（替换整个 Provider）| ①②③④ | 单元 / 快速契约测试、注入失败与时序（取消、路由分支）|
| **B. MockTransport** | ⑤ 发出 HTTP 之后（只换网络传输）| ①②③④⑤ 全部 | 端到端测试：验证上游请求体组装、SSE 解析、供应商错误归一化 |

> **优先用 B。** 网关的核心职责是"协议转换"，最易出 bug 的恰是 ⑤ 层：`response_format`
> 结构拼装、SSE 分块解析（`[DONE]` / usage 尾帧）、上游 429/超时归一化为可重试。
> A 手法在 ⑤ 之前就返回，测不到这些。B 让 ⑤ 真实执行，只把最底层网络传输换成假的。

#### 11.1.2 MockTransport 双层夹心（默认端到端测试骨架）

参考实现见 `course_code/week01/1-7/llm-gateway/tests/conftest.py`。要点：

```
测试 client ──ASGITransport──► Gateway(FastAPI app) ──MockTransport──► handler(伪上游)
```

- **外层** `httpx.ASGITransport(app=app)`：请求直接打进 app，不起真实端口。
- **内层** `httpx.MockTransport(handler)`：注入到网关持有的上游 `http_client`，
  即 `create_app(config, http_client=upstream_client)`（**依赖注入是可测性的前提**，
  §1.1 的 `app.py` 装配必须支持传入 `http_client`）。
- `handler(request) -> httpx.Response`：按 `request.url.host` / 请求体 / 调用计数编排
  伪上游响应，并可**直接对网关真实发出的 request 做断言**。

`make_config(db, **overrides)` 用配置造场景：多供应商 + 优先级路由 + 定价 + 重试
（`base_delay=0`）+ 限流开关；`overrides` 针对性打开单个特性；DB 用 `tmp_path` 隔离。

#### 11.1.3 行为序列断言（不止看最终响应）

除结果外，必须钉死中间行为：

- **重试+fallback**：断言上游调用序列 `[(primary,m1),(primary,m1),(fallback,m2)]` +
  审计 `retries=1 / fallbacks=1 / cost_usd` 精确值。
- **结构化修复**：`handler` 第 1 次返回违反 schema 的 JSON、第 2 次合法，断言第 2 次
  上游请求体里带入了校验失败反馈（出口校验→带错重试的闭环，§4.2）。
- **Prompt 注入**：断言上游收到的 `messages[0]` 正是渲染后的 system 消息（§6）。
- **SSE**：`handler` 返回真实格式 SSE 文本（含 usage 尾帧 + `[DONE]`），断言透传、
  `first_token_ms` 记录、以及取消/断连后停止拉取（§3）。
- **限流**：`handler` 内 `raise AssertionError`，以此证明限流在**到达上游前**就拦截（§7.3）。

#### 11.1.4 何时仍用 FakeAdapter（手法 A）

对"上游 HTTP 无法自然表达"的场景仍用 FakeAdapter 注入：如协程级取消时序
（`cancel_after`）、`asyncio.CancelledError` 传播、以及纯协议层单元测试（不需要起 app）。

#### 11.1.5 统一约定

- 所有异步测试用 `@pytest.mark.asyncio`（不手写 `asyncio.run`）。
- 对外错误体统一为**稳定 code + 安全 message**（§8），测试对 `error.code` 断言而非文案。
- 每个测试独立 `tmp_path` DB / 独立配置，无共享可变全局。

### 11.2 验收标准映射

| 验收项 | 含义 | 对应 §/测试 |
|---|---|---|
| 测试全绿 | AC 覆盖 §1–§7 的语义 | `tests/`（FakeAdapter 全离线）|
| 静态检查通过 | ruff + mypy（严格）无告警 | `pyproject.toml` 配置 |
| Docker 可启动 | build + run + `/healthz` 200 | §10.2 |
| API 可调用 | 四类入口按 §9 契约响应 | `test_api_contract.py` |

| AC | 含义 | 测试文件（建议）|
|---|---|---|
| AC1 | 内部协议 + FakeAdapter 跑通 | `test_protocol.py` / `test_fake_adapter.py` |
| AC2 | 真实 Adapter 契约一致 | `test_adapter_conformance.py`（+ 可选真实脚本）|
| AC3 | Streaming 与取消 | `test_streaming.py` / `test_cancel.py` |
| AC4 | Structured Output | `test_structured.py` |
| AC5 | 路由与 Fallback | `test_routing_fallback.py` |
| AC6 | Prompt Bundle | `test_prompt_bundle.py` |
| AC7 | 治理与观测闭环 | `test_governance.py` / `test_traces.py` |

### 11.3 静态检查基线（写入 pyproject）

- `ruff`（lint + format 检查）；
- `mypy`（`strict`，协议层零 `Any` 泄漏）；
- `pytest`（`pythonpath="."`，`testpaths=["tests"]`，对齐 `mini_code_agent`）。

---

## §12 从单文件到服务的迁移映射（实现速查）

| gateway.py 符号 | 目标模块 |
|---|---|
| `Message/LLMRequest/LLMResponse/Usage` | `protocol/{messages,request,response}.py` |
| `PromptTemplate/PromptSelection` + `PROMPT_TEMPLATES` | `prompts/{bundle,store}.py`（§6，dict → 文件）|
| `ModelConfig` + `MODEL_CONFIGS` + `PRICE_PER_MILLION` | `config/models.yaml` + `config/settings.py`（§2.3）|
| `Provider` Protocol | `adapters/base.py`（§1.2）|
| `OpenAICompatibleProvider` | `adapters/openai_compatible.py`（§2.2）|
| （新增）FakeProvider（原在测试里） | `adapters/fake.py`（§1.3，提升为一等公民）|
| `validate_model` | `routing/policy.py`（§5.2）|
| `is_retryable` | `routing/retry.py`（§5.3）|
| `call_with_fallback` | `routing/executor.py`（§5）|
| `stream_with_fallback` + `encode_sse` | `routing/executor.py` + `protocol/events.py`（§3）|
| `calculate_cost/record_trace/CALL_TRACES` | `governance/{tracing,metrics,quota}.py`（§7）|
| `GatewayError` | `protocol/errors.py`（§8）|
| FastAPI 路由 | `app.py`（§9，依赖注入装配）|

---

## §13 开放问题（Open Questions）

1. 显式取消端点是否要持久化 `request_id → 取消标志`？MVP 用进程内 registry，够用即可。
2. 配额后端默认内存；是否要在本任务内提供 Redis 实现，还是只留接口？建议留接口。
3. `/metrics` 是否强制依赖 `prometheus_client`？建议做成可选依赖，未装则该端点 501。
4. 真实集成测试是否纳入 CI？建议标记 `@pytest.mark.integration`，默认跳过。
