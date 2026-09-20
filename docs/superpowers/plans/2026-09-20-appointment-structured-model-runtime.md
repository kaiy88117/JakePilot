# Appointment Structured Model Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the safe runtime boundary that can evaluate a locally served appointment decision model in shadow mode, promote it to local-first only after validation, and fall back to the existing strong model without changing business permissions.

**Architecture:** Add a standalone `appointment_decision` package containing the immutable decision contract, deterministic guard, OpenAI-compatible local client, and mode-aware gateway. The legacy AppointmentAgent remains authoritative by default; shadow mode records only a privacy-minimized comparison, while local-first mode is disabled until a frozen component report proves the model meets the design thresholds.

**Tech Stack:** Python 3.11, Pydantic 2, requests, pytest, JSONL evidence, existing JakePilot SSE/trace conventions

**Spec:** `docs/superpowers/specs/2026-09-17-ecommerce-agent-architecture-design.md`, sections 5.9 and 11.5

## Global Constraints

- The model may suggest only `ask_user`, `query_slots`, `request_confirmation`, or `finish`; it never chooses arbitrary tool names.
- Model output cannot bypass ownership checks, Tool Registry, confirmation, idempotency, or business guards.
- Only one deterministic repair is allowed: remove a single Markdown code fence and surrounding whitespace.
- Default mode is `disabled`; `shadow` cannot change the authoritative result; `local_first` falls back on timeout, malformed JSON, schema failure, slot conflict, or failed business preconditions.
- Offline tests use fake clients and never call DeepSeek, llama.cpp, Ollama, or private services.
- No training data, model weights, adapters, GGUF files, prompts containing secrets, or raw customer records are committed to the application repository.

## Review Focus

- A local response contains a valid object plus extra prose: reject after the single fence repair instead of extracting a convenient substring.
- A local response changes an already confirmed slot: reject and preserve the confirmed value before fallback.
- Shadow inference times out: return the strong-model result unchanged and record a bounded failure reason.
- `finish` arrives with `confirmation=false`: reject deterministically; never map it to a write action.
- Dataset samples with the same `cluster_id` appear in train and evaluation splits: validation fails before any training command can run.

---

### Task 1: Freeze the structured decision contract and business guard

**Files:**
- Create: `appointment_decision/__init__.py`
- Create: `appointment_decision/contracts.py`
- Create: `appointment_decision/validator.py`
- Test: `tests/test_appointment_decision_contract.py`

**Interfaces:**
- Produces: `AppointmentDecision.model_validate_json(raw: str) -> AppointmentDecision`
- Produces: `validate_decision(decision: AppointmentDecision, confirmed_slots: AppointmentSlots) -> None`, raising `DecisionValidationError`
- Consumes: no earlier task interfaces

- [ ] **Step 1: Write failing contract tests**

Create tests that prove:

```python
def test_contract_rejects_unknown_fields_and_actions():
    payload = {"action": "call_any_tool", "slots": {}, "missing_slots": [], "tool": "refund"}
    with pytest.raises(ValidationError):
        AppointmentDecision.model_validate(payload)


def test_guard_rejects_finish_without_confirmation():
    decision = AppointmentDecision(
        action="finish",
        slots=AppointmentSlots(confirmation=False),
        missing_slots=(),
    )
    with pytest.raises(DecisionValidationError, match="confirmation"):
        validate_decision(decision, AppointmentSlots())


def test_guard_rejects_conflict_with_confirmed_slot():
    decision = AppointmentDecision(
        action="ask_user",
        slots=AppointmentSlots(order_id="JP-OTHER"),
        missing_slots=("region",),
    )
    with pytest.raises(DecisionValidationError, match="order_id"):
        validate_decision(
            decision,
            AppointmentSlots(order_id="JP20260919001"),
        )
```

- [ ] **Step 2: Run the contract tests and verify RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_appointment_decision_contract.py -q`

Expected: collection fails because `appointment_decision` does not exist.

- [ ] **Step 3: Implement the immutable Pydantic contract**

Define models with `ConfigDict(extra="forbid", frozen=True)`:

```python
AppointmentAction = Literal[
    "ask_user", "query_slots", "request_confirmation", "finish"
]

class AppointmentSlots(BaseModel):
    order_id: str | None = None
    product_ref: str | None = None
    service_type: Literal["install", "inspect", "repair"] | None = None
    issue_type: str | None = None
    region: str | None = None
    date_range: str | None = None
    slot_id: str | None = None
    confirmation: bool = False

class AppointmentDecision(BaseModel):
    action: AppointmentAction
    slots: AppointmentSlots
    missing_slots: tuple[Literal[
        "order_id", "product_ref", "service_type", "issue_type",
        "region", "date_range", "slot_id"
    ], ...]

class DecisionRequest(BaseModel):
    message: str
    recent_appointment_history: tuple[str, ...] = ()
    confirmed_slots: AppointmentSlots = AppointmentSlots()
    current_time: datetime
    allowed_actions: tuple[AppointmentAction, ...]
```

Implement `validate_decision` with these exact gates:

- A non-null confirmed slot cannot be changed to a different value.
- Every `missing_slots` entry must still be null in `slots`.
- `query_slots` requires `product_ref`, `service_type`, `region`, and `date_range`.
- `request_confirmation` requires `slot_id`.
- `finish` requires `slot_id` and `confirmation=True`.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_appointment_decision_contract.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit Task 1**

```powershell
git add appointment_decision tests/test_appointment_decision_contract.py
git commit -m "feat: define appointment decision contract"
```

---

### Task 2: Add the local client and mode-aware fallback gateway

**Files:**
- Create: `appointment_decision/client.py`
- Create: `appointment_decision/gateway.py`
- Create: `appointment_decision/trace.py`
- Test: `tests/test_appointment_decision_gateway.py`

**Interfaces:**
- Consumes: `AppointmentDecision`, `AppointmentSlots`, `validate_decision`
- Produces: `LocalDecisionClient.complete(request: DecisionRequest) -> str`
- Produces: `AppointmentDecisionGateway.decide(request: DecisionRequest, fallback: Callable[[DecisionRequest], AppointmentDecision]) -> DecisionOutcome`

- [ ] **Step 1: Write failing gateway tests**

Cover the exact mode matrix with a fake client and a deterministic fallback:

```python
class FakeClient:
    def __init__(self, response: str | None = None, error: Exception | None = None):
        self.response = response
        self.error = error
        self.calls = 0

    def complete(self, request):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.response


def strong_decision(_request):
    return AppointmentDecision(
        action="ask_user",
        slots=AppointmentSlots(product_ref="洗衣机"),
        missing_slots=("region", "date_range"),
    )


def valid_local_json():
    return AppointmentDecision(
        action="query_slots",
        slots=AppointmentSlots(
            product_ref="洗衣机",
            service_type="repair",
            region="杭州市西湖区",
            date_range="2026-09-21/2026-09-22",
        ),
        missing_slots=(),
    ).model_dump_json()


def test_disabled_never_calls_local_client(request):
    client = FakeClient(valid_local_json())
    outcome = AppointmentDecisionGateway("disabled", client).decide(
        request, strong_decision
    )
    assert client.calls == 0
    assert outcome.source == "strong_model"


def test_shadow_returns_fallback_even_when_local_is_valid(request):
    outcome = AppointmentDecisionGateway(
        "shadow", FakeClient(valid_local_json())
    ).decide(request, strong_decision)
    assert outcome.decision == strong_decision(request)
    assert outcome.shadow_decision is not None


def test_local_first_returns_valid_local_decision(request):
    outcome = AppointmentDecisionGateway(
        "local_first", FakeClient(valid_local_json())
    ).decide(request, strong_decision)
    assert outcome.source == "local_model"
    assert outcome.decision.action == "query_slots"


@pytest.mark.parametrize(
    ("client", "reason"),
    [
        (FakeClient(error=TimeoutError()), "timeout"),
        (FakeClient("not-json"), "invalid_json"),
        (
            FakeClient(
                AppointmentDecision(
                    action="ask_user",
                    slots=AppointmentSlots(order_id="JP-OTHER"),
                    missing_slots=("region",),
                ).model_dump_json()
            ),
            "confirmed_slot_conflict",
        ),
    ],
)
def test_local_first_falls_back_on_invalid_local_result(request, client, reason):
    outcome = AppointmentDecisionGateway("local_first", client).decide(
        request.model_copy(
            update={
                "confirmed_slots": AppointmentSlots(order_id="JP20260919001")
            }
        ),
        strong_decision,
    )
    assert outcome.source == "strong_model_fallback"
    assert outcome.trace.fallback_reason == reason


def test_only_one_complete_code_fence_repair_is_allowed(request):
    fenced = "```json\n" + valid_local_json() + "\n```"
    repaired = AppointmentDecisionGateway(
        "local_first", FakeClient(fenced)
    ).decide(request, strong_decision)
    prose = AppointmentDecisionGateway(
        "local_first", FakeClient("result: " + valid_local_json())
    ).decide(request, strong_decision)
    assert repaired.source == "local_model"
    assert prose.source == "strong_model_fallback"
```

Each test must assert both the authoritative result and a safe trace containing only `mode`, `source`, `latency_ms`, `validation_status`, and `fallback_reason`; it must not contain message text, slot values, prompts, or credentials.

- [ ] **Step 2: Run gateway tests and verify RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_appointment_decision_gateway.py -q`

Expected: imports fail because the client and gateway do not exist.

- [ ] **Step 3: Implement the OpenAI-compatible local client**

`LocalDecisionClient` reads these settings through constructor arguments, with environment configuration applied only in a `from_env()` factory:

```python
endpoint = "http://127.0.0.1:8080/v1/chat/completions"
model = "appointment-decision-q4_k_m"
timeout_seconds = 4.0
```

The request contains one system instruction defining the JSON contract and one user data object with `message`, `recent_appointment_history`, `confirmed_slots`, `current_time`, and `allowed_actions`. Call `self.session.post(self.endpoint, json=payload, headers=headers, timeout=self.timeout_seconds)` and read `choices[0].message.content`; never log headers or content.

- [ ] **Step 4: Implement gateway modes and safe trace**

Define:

```python
DecisionMode = Literal["disabled", "shadow", "local_first"]

class DecisionOutcome(BaseModel):
    decision: AppointmentDecision
    source: Literal["strong_model", "local_model", "strong_model_fallback"]
    trace: DecisionTrace
    shadow_decision: AppointmentDecision | None = None
```

For `disabled`, call only fallback. For `shadow`, call fallback and independently evaluate local output, but always return fallback as `decision`. For `local_first`, parse and validate local output, otherwise call fallback exactly once. Strip a single complete code fence only when the entire response is fenced; reject prose before or after JSON.

- [ ] **Step 5: Run gateway tests and verify GREEN**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_appointment_decision_gateway.py -q`

Expected: all tests pass without network access.

- [ ] **Step 6: Commit Task 2**

```powershell
git add appointment_decision tests/test_appointment_decision_gateway.py
git commit -m "feat: add appointment model fallback gateway"
```

---

### Task 3: Build dataset validation and leakage gates

**Files:**
- Create: `appointment_training/__init__.py`
- Create: `appointment_training/dataset.py`
- Create: `scripts/validate_appointment_dataset.py`
- Create: `docs/appointment-training-data-format.md`
- Test: `tests/test_appointment_training_dataset.py`

**Interfaces:**
- Consumes: `AppointmentDecision`
- Produces: `validate_dataset(path: Path) -> DatasetManifest`
- Produces CLI: `python -m scripts.validate_appointment_dataset --input <jsonl>`

- [ ] **Step 1: Write failing dataset tests**

Test valid SFT records and reject:

- duplicate `sample_id`;
- the same `cluster_id` in both `train` and `eval`;
- invalid `AppointmentDecision` output;
- phone numbers matching `1[3-9]\d{9}`;
- email addresses;
- full street address fields;
- an evaluation set with fewer than 150 samples when `--formal` is used.

- [ ] **Step 2: Run dataset tests and verify RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_appointment_training_dataset.py -q`

Expected: collection fails because `appointment_training` does not exist.

- [ ] **Step 3: Implement JSONL validation and manifest output**

Each row must contain:

```json
{
  "sample_id": "apt-000001",
  "cluster_id": "template-relative-time-repair-01",
  "split": "train",
  "messages": [{"role": "user", "content": "周六想约洗衣机维修"}],
  "decision": {"action": "ask_user", "slots": {}, "missing_slots": ["region", "date_range"]},
  "source_type": "licensed_public | anonymous_template | human_authored",
  "license_ref": "dataset-card-id"
}
```

Return counts by split/source/action plus a SHA-256 digest over normalized rows. The CLI prints JSON and exits non-zero on any validation error. Documentation states that raw data and generated corpora live outside the application Git repository.

- [ ] **Step 4: Run dataset tests and verify GREEN**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_appointment_training_dataset.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit Task 3**

```powershell
git add appointment_training scripts/validate_appointment_dataset.py docs/appointment-training-data-format.md tests/test_appointment_training_dataset.py
git commit -m "feat: validate appointment training datasets"
```

---

### Task 4: Add component evaluation and promotion evidence

**Files:**
- Create: `appointment_training/evaluator.py`
- Create: `appointment_training/evidence.py`
- Create: `scripts/run_appointment_model_eval.py`
- Test: `tests/test_appointment_model_evaluator.py`

**Interfaces:**
- Consumes: frozen evaluation JSONL accepted by `validate_dataset`
- Consumes: callable `predict(DecisionRequest) -> str`
- Produces: `AppointmentModelReport` with structure validity, slot Exact Match, field F1, action accuracy, hallucinated-slot rate, refusal-boundary rate, local-decision P95 latency, and fallback rate

- [ ] **Step 1: Write failing evaluator tests**

Use a five-case in-memory fixture with one malformed JSON output, one wrong action, one hallucinated slot, and two correct decisions. Assert the exact confusion counts and derived rates. Add a test proving report evidence contains dataset digest, code revision, prompt version, model version, hardware label, and aggregate metrics but no raw user content.

- [ ] **Step 2: Run evaluator tests and verify RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_appointment_model_evaluator.py -q`

Expected: imports fail because the evaluator does not exist.

- [ ] **Step 3: Implement deterministic component metrics**

Calculate metrics from parsed `AppointmentDecision` objects. Count a non-null predicted slot absent from the reference as hallucinated. Treat malformed output as failed structure, action, and slots; never drop it from the denominator. Compute `p95_local_latency_ms` from all local prediction attempts with nearest-rank selection. End-to-end fallback latency belongs to the later integration benchmark and must not be presented as this component metric.

- [ ] **Step 4: Implement promotion decision**

The report emits `promotion_eligible=true` only when all configured thresholds pass. The default thresholds mirror the spec: structured validity at least 0.98, slot Exact Match at least 0.90, action accuracy at least 0.90, hallucinated-slot rate at most 0.02, and formal evaluation size at least 150. A development report with fewer cases always emits `formal_benchmark=false` and `promotion_eligible=false`.

- [ ] **Step 5: Run evaluator tests and verify GREEN**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_appointment_model_evaluator.py -q`

Expected: all tests pass.

- [ ] **Step 6: Commit Task 4**

```powershell
git add appointment_training scripts/run_appointment_model_eval.py tests/test_appointment_model_evaluator.py
git commit -m "feat: evaluate appointment decision models"
```

---

### Task 5: Wire shadow mode into the service appointment seam

**Files:**
- Modify: `agents/appointment/input_parser.py`
- Modify: `agents/appointment_agent.py`
- Modify: `.env.example`
- Modify: `docs/DEMO_GUIDE.md`
- Test: `tests/test_appointment_decision_integration.py`

**Interfaces:**
- Consumes: `AppointmentDecisionGateway.decide(request, fallback) -> DecisionOutcome`
- Produces: a `decision_model_trace` runtime event containing only safe trace fields

- [ ] **Step 1: Write failing integration tests**

Prove that default configuration does not call the local endpoint, shadow mode cannot change the legacy strong-model decision, local-first falls back on invalid output, and neither SSE nor logs contain prompt text, slot values, endpoint credentials, or hidden reasoning.

- [ ] **Step 2: Run integration tests and verify RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_appointment_decision_integration.py -q`

Expected: tests fail because AppointmentAgent has no decision gateway seam.

- [ ] **Step 3: Add an optional gateway seam without replacing business guards**

Construct the gateway once per AppointmentAgent. `disabled` preserves the current path byte-for-byte. `shadow` runs the local decision after the strong-model parse and emits a comparison trace, but passes only the strong-model fields to `AppointmentProcessor`. `local_first` may provide the normalized decision only when the latest formal evidence report has `promotion_eligible=true`; otherwise initialization downgrades to `shadow` and records `formal_evidence_missing`.

- [ ] **Step 4: Document exact environment switches**

Add:

```dotenv
APPOINTMENT_DECISION_MODE=disabled
APPOINTMENT_LOCAL_BASE_URL=http://127.0.0.1:8080/v1
APPOINTMENT_LOCAL_MODEL=appointment-decision-q4_k_m
APPOINTMENT_LOCAL_TIMEOUT_SECONDS=4
APPOINTMENT_MODEL_EVIDENCE_DIR=artifacts/appointment-model/reports
```

Document that `disabled` is the demo default, `shadow` is safe for comparison, and `local_first` remains gated until a formal 150-case report is present.

- [ ] **Step 5: Run integration and focused regression tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_appointment_decision_integration.py tests\test_appointment_agent.py tests\test_stream_protocol.py -q
```

Expected: new integration tests pass; any pre-existing legacy appointment failures are reported separately and unchanged.

- [ ] **Step 6: Commit Task 5**

```powershell
git add agents/appointment/input_parser.py agents/appointment_agent.py .env.example docs/DEMO_GUIDE.md tests/test_appointment_decision_integration.py
git commit -m "feat: add appointment model shadow mode"
```

---

## Final verification

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_appointment_decision_contract.py tests/test_appointment_decision_gateway.py tests/test_appointment_training_dataset.py tests/test_appointment_model_evaluator.py tests/test_appointment_decision_integration.py -q
.\.venv\Scripts\python.exe -m compileall appointment_decision appointment_training agents scripts
git diff --check
```

Then run the repository-wide suite and compare its failure set with the known baseline. Do not claim model quality, speed, memory savings, or promotion eligibility until a real frozen dataset, trained model, target hardware, and generated evidence report exist.
