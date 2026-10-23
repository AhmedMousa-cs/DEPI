class InjectedFailure(RuntimeError):
    """Raised when a configured failure point is reached."""


class FailureInjector:
    def __init__(self, fail_at=None):
        self.fail_at = fail_at

    def hit(self, point):
        if self.fail_at == point:
            raise InjectedFailure(
                f"Injected failure at: {point}"
            )
