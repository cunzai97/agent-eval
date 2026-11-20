class Scheduler:
    def __init__(self, worker):
        self.worker = worker

    def start_prompt(self, seq, restored_position, chunks):
        # This worked for cold prompts, but checkpoint suffixes start non-zero.
        if restored_position == 0:
            self.worker.prompt_begin(seq, restored_position)
        for chunk in chunks:
            self.worker.enqueue_prompt(seq, chunk)
        self.worker.prompt_finish(seq)

    def generate(self, seq, batch_size):
        self.worker.verify_generation(seq, range(batch_size))

    def cancel(self, seq):
        self.worker.cancel(seq)
