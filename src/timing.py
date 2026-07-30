"""Request-scoped latency measurements for local model orchestration."""

from dataclasses import dataclass
from time import perf_counter


@dataclass(slots=True)
class RequestTiming:
    """Collect monotonic timings without changing execution decisions."""

    started_at: float
    prepare_completed_at: float | None = None
    first_http_request_at: float | None = None
    first_byte_at: float | None = None
    first_token_at: float | None = None
    model_completed_at: float | None = None
    request_ms: float = 0.0
    parse_ms: float = 0.0
    validation_ms: float = 0.0
    action_ms: float = 0.0

    @classmethod
    def start(cls) -> "RequestTiming":
        return cls(started_at=perf_counter())

    def mark_prepare_complete(self) -> None:
        self.prepare_completed_at = perf_counter()

    def mark_http_request_start(self) -> None:
        if self.first_http_request_at is None:
            self.first_http_request_at = perf_counter()

    def mark_first_byte(self) -> None:
        if self.first_byte_at is None:
            self.first_byte_at = perf_counter()

    def mark_first_token(self) -> None:
        if self.first_token_at is None:
            self.first_token_at = perf_counter()

    def mark_model_complete(self) -> None:
        self.model_completed_at = perf_counter()

    def add_parse(self, started_at: float) -> None:
        self.parse_ms += (perf_counter() - started_at) * 1000

    def add_validation(self, started_at: float) -> None:
        self.validation_ms += (perf_counter() - started_at) * 1000

    def add_action(self, started_at: float) -> None:
        self.action_ms += (perf_counter() - started_at) * 1000

    def elapsed_ms(self, timestamp: float | None) -> int | None:
        return None if timestamp is None else round((timestamp - self.started_at) * 1000)

    def log(self) -> None:
        total_ms = round((perf_counter() - self.started_at) * 1000)
        prepare_ms = self.elapsed_ms(self.prepare_completed_at)
        http_request_start_ms = self.elapsed_ms(self.first_http_request_at)
        first_byte_ms = self.elapsed_ms(self.first_byte_at)
        first_token_ms = self.elapsed_ms(self.first_token_at)
        completion_ms = self.elapsed_ms(self.model_completed_at)
        model_window_ms = (
            None
            if self.first_http_request_at is None or self.model_completed_at is None
            else round((self.model_completed_at - self.first_http_request_at) * 1000)
        )
        outside_model_ms = None if model_window_ms is None else max(total_ms - model_window_ms, 0)
        print("[TIMING]")
        print(f"prepare_ms={prepare_ms}")
        print(f"http_request_start_ms={http_request_start_ms}")
        print(f"request_ms={round(self.request_ms)}")
        print(f"first_byte_ms={first_byte_ms}")
        print(f"first_token_ms={first_token_ms}")
        print(f"completion_ms={completion_ms}")
        print(f"parse_ms={round(self.parse_ms)}")
        print(f"validation_ms={round(self.validation_ms)}")
        print(f"action_ms={round(self.action_ms)}")
        print(f"total_ms={total_ms}")
        print(f"lm_studio_window_ms={model_window_ms}")
        print(f"outside_lm_studio_ms={outside_model_ms}")
        if model_window_ms is not None and outside_model_ms is not None and outside_model_ms > model_window_ms:
            preparation_ms = prepare_ms or 0
            stages = {
                "request preparation": preparation_ms,
                "JSON parsing": round(self.parse_ms),
                "schema validation": round(self.validation_ms),
                "action execution": round(self.action_ms),
            }
            accounted_ms = sum(stages.values())
            stages["unattributed orchestration"] = max(outside_model_ms - accounted_ms, 0)
            dominant_stage = max(stages, key=stages.__getitem__)
            print(
                "[TIMING] majority latency is outside LM Studio; "
                f"dominant stage={dominant_stage} ({stages[dominant_stage]}ms)"
            )
