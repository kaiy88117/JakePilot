"""In-memory trace recorder with a strict public projection."""

from runtime.contracts import RuntimeEvent


_PUBLIC_DATA_FIELDS = frozenset(
    {
        "tool",
        "status",
        "step",
        "route",
        "reason",
        "elapsed_ms",
        "external_ref",
    }
)


class TraceRecorder:
    def __init__(self, trace_id: str) -> None:
        if not trace_id.strip():
            raise ValueError("trace_id must not be blank")
        self.trace_id = trace_id.strip()
        self._events: list[RuntimeEvent] = []

    def record(self, event: RuntimeEvent) -> None:
        self._events.append(event.model_copy(deep=True))

    def snapshot(self, public: bool = False) -> list[RuntimeEvent]:
        if not public:
            return [event.model_copy(deep=True) for event in self._events]

        return [
            RuntimeEvent(
                type=event.type,
                data={
                    key: value
                    for key, value in event.data.items()
                    if key in _PUBLIC_DATA_FIELDS
                },
            )
            for event in self._events
        ]
