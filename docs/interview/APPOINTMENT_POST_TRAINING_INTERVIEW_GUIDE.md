# 预约决策模型后训练面试手册

## 1. 当前状态与表达边界

JakePilot 已经实现预约决策模型的运行和评测骨架，包括固定输入输出契约、确定性业务校验、`disabled`、`shadow`、`local_first` 三态网关、本地模型失败后的强模型回退、训练数据隐私与泄漏检查、组件评测指标和脱敏 Trace。

真实的 SFT、DPO、模型合并、GGUF 量化和冻结集对比尚未执行。因此，面试时可以说“完成后训练方案、运行接口和评测门禁设计”，不能说“已经训练并取得某个提升”。文中的指标均为验收目标，不是实测结果。

当前代码入口：

- `appointment_decision/contracts.py`：预约决策的不可变 Schema。
- `appointment_decision/validator.py`：确定性业务 Guard。
- `appointment_decision/gateway.py`：本地模型、Shadow 和强模型回退。
- `appointment_training/dataset.py`：JSONL、隐私、许可证和数据泄漏校验。
- `appointment_training/evaluator.py`：结构、槽位、动作和幻觉指标。
- `scripts/validate_appointment_dataset.py`：训练前数据检查。
- `scripts/run_appointment_model_eval.py`：对本地推理服务运行冻结集评测。

## 2. 面试时先讲清楚为什么只训练这个组件

任务路由、知识问答、异常恢复和最终回复需要较强的通用理解能力，直接微调整个 Agent 会扩大数据规模和回归范围。预约场景中的槽位抽取与下一动作选择有固定字段、固定动作和明确错误类型，适合交给小模型。

本方案让本地模型只做两件事：

1. 从当前请求、预约相关历史和已确认状态中抽取槽位。
2. 在允许动作中选择 `ask_user`、`query_slots`、`request_confirmation` 或 `finish`。

本地模型不直接调用工具，不修改数据库，也不决定是否绕过用户确认。Runtime 将动作映射到白名单流程，Guard 再校验订单归属、必填字段、确认状态和时段有效性。

```text
用户消息与预约上下文
        |
        v
本地预约决策模型
        |
        v
JSON Schema 与业务 Guard
   | 合法                 | 非法、超时、冲突
   v                      v
白名单动作映射       deepseek-flash 回退
        \                /
         v              v
      预约查询、确认和写入工具
                 |
                 v
         Trace、评测证据与最终回复
```

## 3. 输入输出契约如何设计

模型输入只保留当前消息、最近几轮预约相关历史、已确认槽位、当前时间和允许动作。订单详情、支付信息、完整用户画像和无关 RAG 片段不进入模型输入。

输出固定为一个 JSON 对象，核心字段如下：

```json
{
  "action": "ask_user | query_slots | request_confirmation | finish",
  "slots": {
    "order_id": null,
    "product_ref": "洗衣机",
    "service_type": "repair",
    "issue_type": "无法排水",
    "region": "杭州市西湖区",
    "date_range": "2026-09-21/2026-09-22",
    "slot_id": null,
    "confirmation": false
  },
  "missing_slots": ["order_id", "slot_id"]
}
```

Schema 禁止额外字段。`finish` 必须已经确认并包含时段，`query_slots` 必须具备商品、服务类型、区域和日期，模型也不能改写已经确认的槽位。输出不合法时只允许去除完整 Markdown 代码围栏这一类不改变语义的修复，之后仍不合法就回退强模型。

## 4. 后训练实施步骤

### 步骤 0：冻结任务和版本

固定四类动作、槽位定义、业务规则、系统指令和评测口径。记录代码提交、Prompt 版本、基础模型、数据哈希和硬件，防止训练前后悄悄改变标准。

候选基础模型使用 Qwen3-1.7B，并保留 Qwen3-0.6B 作为速度和资源对照。当前电脑是 GTX 1650 4GB，不把它作为 1.7B 训练环境；训练使用 16GB 或 24GB 云显卡，本机负责数据检查、GGUF 推理和演示。

### 步骤 1：先建立冻结评测集

先准备不少于 150 条评测样本，再开始生成训练数据。建议分布如下：

| 类型 | 数量 | 重点检查 |
|---|---:|---|
| 槽位完整请求 | 30 | 能否直接进入时段查询 |
| 信息缺失与追问 | 30 | 是否只追问必要字段 |
| 可查询时段动作 | 30 | 动作和关键参数是否正确 |
| 多轮纠正与确认 | 20 | 是否保留已确认字段并接受纠正 |
| 相对时间与口语噪声 | 20 | “明天下午”“周末”等时间归一化 |
| 越权与臆造诱导 | 20 | 是否拒绝跳过确认或编造槽位 |

评测集按表达模板、商品或订单实体和对话簇隔离。相同模板仅替换日期或商品的样本必须属于同一 `cluster_id`，不能一部分进训练集、一部分进评测集。

数据保存为 UTF-8 JSONL，每条记录都带 `sample_id`、`cluster_id`、`split`、`source_type` 和 `license_ref`。训练前执行：

```powershell
.\.venv\Scripts\python.exe -m scripts.validate_appointment_dataset `
  --input <数据集.jsonl> `
  --formal
```

只有 Schema、隐私、许可证字段、业务标签和跨分区泄漏全部通过，才能生成冻结数据清单和 SHA-256。

### 步骤 2：构建 SFT 数据

首轮目标为至少 1,000 条 SFT 样本。数据来自有明确许可证的公开对话数据、匿名业务模板和人工编写样例，不使用真实姓名、手机号、详细地址、订单或支付信息。

SFT 数据需要覆盖完整请求、缺失字段、多轮补充、用户改口、无关问题、相对时间、非法要求和确认流程。输出统一为合法决策 JSON，训练时使用模型原生 Chat Template 和 Response-only Loss，只对助手输出计算损失。

`deepseek-flash` 可以生成候选问题和候选标签，但不能直接把生成结果作为金标。候选数据依次通过 Pydantic Schema、业务 Guard、重复检查和人工抽检。高风险样本需要全部人工确认，普通样本按簇抽检。

### 步骤 3：构建 DPO 偏好对

首轮目标为约 300 对 Chosen 和 Rejected。DPO 不重复教基础格式，主要处理 SFT 后仍存在的边界错误：

- Chosen 保留已确认槽位，Rejected 擅自覆盖槽位。
- Chosen 继续追问缺失信息，Rejected 编造订单号或时段。
- Chosen 请求用户确认，Rejected 未经确认就选择 `finish`。
- Chosen 输出单个合法 JSON，Rejected 带解释、代码围栏或多余字段。
- Chosen 正确理解相对时间，Rejected 使用过期或越界时间。

每对数据保存错误标签，便于观察 DPO 到底修复了哪类错误。若 DPO 导致模型过度追问或拒绝，应回查偏好对中是否把正常执行样本错误标成 Rejected。

### 步骤 4：运行 Base 与 SFT 对照

先对未训练的 Base 模型执行同一冻结集，保存初始报告。随后用 LLaMA-Factory 进行 QLoRA SFT，建议从以下保守配置开始：

- 4-bit 量化加载。
- 最大序列长度 1,024，确认上下文确实需要后再增加。
- Micro Batch Size 为 1，通过梯度累积形成有效 Batch。
- 开启 Gradient Checkpointing。
- 学习率从 `1e-4` 附近做小范围搜索。
- 训练 2 到 3 个 Epoch，并按槽位 Exact Match 选择 Checkpoint。
- 关闭长思维输出，目标是短 JSON，不训练隐藏推理过程。

每次实验只改变少量变量，记录数据版本、随机种子、LoRA Rank、学习率、Epoch、最大长度和 Checkpoint。不能只记录表现最好的那一次。

### 步骤 5：决定是否执行 DPO

SFT 必须同时改善结构有效率、槽位准确率和动作准确率，才进入 DPO。若 SFT 已经满足边界要求，可以暂缓 DPO；若主要错误集中在未经确认执行、编造字段或错误结束，DPO 才有明确价值。

DPO 后重点观察拒绝边界、臆造槽位率和错误结束率，同时要求结构有效率和槽位 Exact Match 相比 SFT 下降不超过 1 个百分点。出现明显下降时，先检查偏好对质量，不通过提高训练轮数强行拟合。

### 步骤 6：合并、转换与量化

将 LoRA Adapter 与基础模型合并，转换为 GGUF，再生成 Q4_K_M。量化前后的模型使用同一冻结集和 Prompt 评测。槽位和动作准确率下降超过 1.5 个百分点时，不进入演示默认路径，可以尝试 Q5_K_M、缩短上下文或重新检查 Chat Template。

本地用 `llama.cpp` 暴露 OpenAI 兼容接口，服务别名与环境变量保持一致。应用只访问 `/v1/chat/completions`，不依赖训练框架。

### 步骤 7：运行组件评测

设置本地服务地址与模型名后执行：

```powershell
$env:APPOINTMENT_LOCAL_BASE_URL = "http://127.0.0.1:8080/v1"
$env:APPOINTMENT_LOCAL_MODEL = "appointment-decision-q4_k_m"

.\.venv\Scripts\python.exe -m scripts.run_appointment_model_eval `
  --input <冻结评测集.jsonl> `
  --output-dir artifacts/appointment-model/reports `
  --code-revision <提交哈希> `
  --prompt-version appointment-decision-v1 `
  --model-version <模型与量化版本> `
  --hardware-label <显卡与内存说明> `
  --formal
```

核心指标包括 Structured Output Validity、Slot Exact Match、Field F1、Action Accuracy、Hallucinated Slot Rate、Refusal Boundary Rate、Fallback Rate 和 P95 Local Latency。当前设计门槛是结构有效率不低于 98%，槽位 Exact Match 和动作准确率不低于 90%，臆造槽位率不高于 2%。这些是放行条件，不是当前实测数据。

### 步骤 8：Shadow 验证

组件指标达标后仍不直接接管线上决策。先启用 `shadow`：远端强模型继续产生实际决策，本地模型同步运行，只记录脱敏后的来源、延迟、校验状态和回退原因。

Shadow 阶段分析本地模型与强模型的分歧，人工复核高风险差异。这里不能默认强模型一定正确，需要结合业务规则和金标判断。分歧样本可以进入下一轮数据建设，但必须经过隐私处理和重新分簇。

### 步骤 9：迁移 Local First 执行顺序

当前旧预约处理器会先调用强模型，再执行 Shadow，本地模型即使通过组件评测也不会真正节省远端调用。正式启用前需要调整顺序：

1. 先组装 `DecisionRequest` 并调用本地模型。
2. 通过 Schema 和业务 Guard 后，直接映射到预约动作。
3. 超时、非法 JSON、已确认槽位冲突或业务前置条件不满足时，再调用 `deepseek-flash`。
4. 两个模型都失败时保留已确认状态，向用户追问或转人工，不猜测后继续写入。

端到端评测还要验证预约成功率、平均追问轮数、回退率、P95 延迟、重复写入和调用成本。本地优先路径的预约成功率不能比全程强模型低超过 1 个百分点，否则继续保持 Shadow。

## 5. 可能遇到的问题与处理方法

| 问题 | 常见原因 | 如何发现 | 处理方法 |
|---|---|---|---|
| 输出不是合法 JSON | 训练数据包含解释文本，Chat Template 不一致 | Structured Output Validity 下降 | 清洗标签，使用 Response-only Loss，固定系统指令，Guard 失败后回退 |
| 模型编造订单号或时段 | 数据中过多完整样本，缺少信息不足场景 | Hallucinated Slot Rate 升高 | 增加缺失字段样本和对应 DPO 对，缺失时只允许 `ask_user` |
| 覆盖已确认槽位 | 输入没有显式给出确认状态，纠正样本不足 | confirmed slot conflict 增多 | 输入携带 `confirmed_slots`，训练纠正与冲突样本，Guard 拒绝覆盖 |
| “明天下午”解析错误 | 缺少当前时间、时区或口语时间样本 | 相对时间子集准确率低 | 输入固定 `current_time`，统一 ISO 日期范围，增加跨月和跨年样本 |
| 评测分数虚高 | 相似模板随机切分到训练集和评测集 | 新表达明显变差 | 按模板、实体和对话簇隔离，校验 `cluster_id` 泄漏 |
| 动作类别偏斜 | `ask_user` 或 `query_slots` 占比过高 | 混淆矩阵集中在一个动作 | 按动作和错误类型配额采样，不只看总体准确率 |
| DPO 后过度拒绝 | Rejected 数据把正常执行误标为风险 | Refusal Rate 上升，任务成功率下降 | 回查偏好对，加入正常执行 Chosen，降低 DPO 强度或暂不采用 DPO |
| DPO 后格式能力下降 | 偏好目标压过 SFT 的结构约束 | JSON 与槽位指标下降 | 缩小 DPO 学习率和 Epoch，混入格式稳定样本，触发 1 个百分点门槛即停止 |
| Q4 量化后准确率明显下降 | 小模型对量化敏感，模板或转换错误 | 与合并模型同集对照失败 | 检查 tokenizer 和 Chat Template，尝试 Q5_K_M，不强行上线 Q4 |
| GTX 1650 训练 OOM | 4GB 显存不足以稳定训练 1.7B | 加载或反向传播时显存溢出 | 云端完成训练，本机只推理；流程验证可改用 0.6B 和短序列 |
| 本地推理超时 | 上下文过长、CPU Offload 过多或并发过高 | P95 和 Timeout Rate 升高 | 缩短预约历史，限制上下文和并发，增加 GPU Offload，保留 4 秒回退 |
| Shadow 与强模型经常不一致 | 标签口径、Prompt 或动作定义不一致 | 分歧率持续偏高 | 按错误类型抽样复核，修订标签与契约，不把强模型输出直接当金标 |
| 敏感信息进入训练集 | 直接使用真实客服或订单记录 | 隐私校验失败 | 只用公开许可数据和匿名模板，拦截手机号、邮箱和详细地址字段 |
| 指标无法复现 | 没记录代码、Prompt、数据和硬件版本 | 同一模型多次报告无法对齐 | 报告绑定数据哈希、提交、Prompt、模型、量化与硬件信息 |
| 回退路径掩盖小模型缺陷 | 只看端到端成功率，没有单独统计回退 | 成功率高但本地命中率很低 | 同时报告组件指标、Fallback Rate 和本地实际接管比例 |

## 6. 面试时如何回答关键追问

### 为什么选 Qwen3-1.7B

任务是固定 Schema 下的槽位抽取和四分类动作选择，不需要大模型完成完整客服推理。1.7B 级别模型有机会在质量与本地延迟之间取得平衡，0.6B 作为资源对照。最终选择看冻结集、延迟和内存，不按参数规模下结论。

### SFT 和 DPO 分别解决什么

SFT 教会模型任务格式和基本映射，包括合法 JSON、时间归一化、槽位抽取和下一动作。DPO 处理边界取舍，比如不编造槽位、不覆盖确认信息、未经确认不结束。SFT 不达标时先修数据和监督训练，不能用 DPO 掩盖基础能力问题。

### 为什么还需要 Guard

模型概率输出不能替代业务约束。即使评测准确率很高，也可能在一次请求里覆盖已确认时段或跳过确认。Schema 和 Guard 是确定性安全边界，模型只给建议，写操作仍由权限、确认和幂等机制控制。

### 为什么先 Shadow

组件评测只证明模型会做局部决策，不能证明它接入旧流程后仍然正确。Shadow 不改变真实执行结果，可以验证延迟、分歧、回退原因和上下文组装，同时降低直接切流风险。

### 为什么不直接训练整个 Agent

整个 Agent 涉及路由、RAG、工具调用、异常恢复和自然语言回答，数据与回归范围太大。预约决策接口窄、标签稳定、指标可确定计算，适合作为本地小模型的第一项任务。

### 当前项目真实做到了哪里

已完成契约、Guard、三态 Gateway、强模型回退、隐私和泄漏校验、组件评测脚本以及 Shadow 接入。尚未完成真实数据构建、SFT、DPO、GGUF 模型产物和 Local First 的端到端切换，因此目前不会在简历中填写后训练提升指标。

## 7. 一个可执行的时间安排

| 时间 | 工作内容 | 产物 |
|---|---|---|
| 第 1 天 | 冻结 150 条评测集，完成数据分簇和校验 | Eval JSONL、Manifest、Base 报告 |
| 第 2 天 | 生成并审核不少于 1,000 条 SFT 数据 | SFT JSONL、抽检记录 |
| 第 3 天 | 云端 QLoRA SFT，多组小范围参数对照 | Adapter、训练日志、SFT 报告 |
| 第 4 天 | 根据错误类型构建 DPO 对并训练 | DPO 数据、Adapter、对照报告 |
| 第 5 天 | 合并、GGUF 量化、本地评测和 Shadow 接入 | GGUF、正式报告、演示 Trace |

一天内只能完成小数据训练流程验证，不能覆盖数据审核、DPO、量化和端到端门禁。若时间有限，优先完成冻结集、SFT 和 Shadow，DPO 根据错误分析决定是否追加。

## 8. 简历与面试口径

当前可用的真实表述：

> 为预约 Agent 设计本地结构化决策模型方案，定义槽位抽取与下一动作契约，完成 Shadow Gateway、确定性业务 Guard、强模型回退，以及训练数据隐私、泄漏校验和冻结集评测骨架。

实际完成训练并生成报告后，才能改为：

> 围绕预约槽位抽取和下一动作选择构建 SFT 与 DPO 数据，完成 Qwen3-1.7B 的 QLoRA 后训练、GGUF 量化和本地推理接入，并通过冻结集与端到端评测验证结构有效率、槽位准确率、动作准确率、回退率和延迟。

第二种表述中的数据规模和指标必须来自对应的数据 Manifest 与评测报告。设计目标、模拟值和单次最好结果不能写成项目成果。

## 9. 参考资料

- Qwen3：<https://github.com/QwenLM/Qwen3>
- LLaMA-Factory：<https://github.com/hiyouga/LLaMA-Factory>
- llama.cpp Server：<https://github.com/ggml-org/llama.cpp/tree/master/tools/server>
