class TransportAdapter:
    def __init__(self, scheduler):
        self.scheduler = scheduler

    def incomplete_stream(self, work):
        # Both transport and scheduler used to retry, duplicating one operation.
        first = self.scheduler.transport_error(work)
        second = self.scheduler.transport_error(work)
        return first or second
